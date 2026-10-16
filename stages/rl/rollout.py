# -*- coding: utf-8 -*-
"""rollout：policy 模型在 AWM 环境里执行任务，产出 trajectory（供 GRPO 训练 / 评估）。

批量版（batch generate）：同一 task 的 group_size 条轨迹同步推进 step，每个 step 把活跃轨迹的
prompt 合并成 batch 一次 generate（GPU 并行），解决多线程 batch=1 串行 generate 导致的
GPU 利用率低（memory-bound）。

复用 lib.runtime 的 XML 工具协议函数（parse_tool_calls / parse_call_tool_arguments /
format_tools_for_response / format_tool_detail / find_tool_by_name）。
提示词用本文件自定义的 _get_system_prompt（多标签版：允许一次回复含多个标签块）。
"""

import torch
from textwrap import dedent
from loguru import logger
from transformers import StoppingCriteria, StoppingCriteriaList
from lib.environment_context import CURRENT_USER_ID
from lib.interrupt import check_cancelled

from lib.runtime import (
    parse_tool_calls,
    parse_call_tool_arguments,
    format_tools_for_response,
    format_tool_detail,
    find_tool_by_name,
)


class _InterruptOnCancel(StoppingCriteria):
    """每个生成步检查取消，抛出后由外层 finally 关闭 rollout 环境。"""

    def __call__(self, input_ids, scores, **kwargs):
        check_cancelled()
        return False


def _get_system_prompt(max_steps: int = 10) -> str:
    """构造发给 LLM 的系统提示词：三个元工具 + 两标签协议（tool_call / final_answer，每步一个工具）。"""
    tools_str = dedent("""\
        1. list_tools
            - Description: List all available MCP tools in the current environment (name + one-line description only).
            - Arguments: None
            - Output: A list of tool names and their one-line descriptions

        2. get_tool_detail_of
            - Description: Get the detailed parameters (input schema) of ONE specific tool. You MUST call this BEFORE calling that tool for the first time, so you never guess its parameters.
            - Arguments:
                - tool_name: str, required, the name of the tool you want to inspect
            - Output: The full input schema (parameters, types, required/optional, descriptions) of that tool

        3. call_tool
            - Description: Call a MCP environment-specific tool
            - Arguments:
                - tool_name: str, required, the tool name in the list_tools output
                - arguments: str, required, the arguments for calling <tool_name>. You must pass a valid JSON string without any markdown fences or additional commentary. This JSON str will be parsed by the tool and executed. You can pass an empty JSON str if no arguments are required by <tool_name>.
            - Output: The result of the <tool_name> tool call""")

    return dedent(f"""\
        # MCP Tools

        You are at a MCP environment. You need to call MCP tools to assist with the user query. You have already logged in, and your user id is {CURRENT_USER_ID} if required for the MCP tool.

        You are provided with THREE functions within <tools></tools> XML tags:
        <tools>
        {tools_str}
        </tools>

        Working rules (IMPORTANT):
        - You MUST always call list_tools FIRST to discover the available tools, and only call it once.
        - Before calling a specific tool (call_tool) for the FIRST time, you MUST call get_tool_detail_of for that tool to learn its parameters. Do NOT guess the parameters.
        - You may call the same tool again later without re-querying its detail.

        In each step, you MUST output EXACTLY ONE of the following two tags:

        1. <awm_tool_call>{{"name": <function-name>, "arguments": <args-json-object>}}</awm_tool_call>
           → Call ONE tool (list_tools / get_tool_detail_of / call_tool). Call only ONE tool per step.

        2. <final_answer>...</final_answer>
           → Your final answer. Use this ONLY when the task is FULLY completed (and you have no more tools to call).

        HOW TO CALL A BUSINESS TOOL (call_tool) — WORKED EXAMPLE:
        - Business tools are called through the SAME "<awm_tool_call>" tag. The only difference from list_tools/get_tool_detail_of is that the business tool name and its arguments go INSIDE the "arguments" field of call_tool.
        - call_tool's "arguments" field is a JSON **string** (a JSON object serialized as text), so the inner quotes must be escaped.
        - Example: to call the business tool "create_applicant" with {{"name": "Alice", "age": 30}}:

        <awm_tool_call>
        {{"name": "call_tool", "arguments": {{"tool_name": "create_applicant", "arguments": "{{\\\"name\\\": \\\"Alice\\\", \\\"age\\\": 30}}"}}}}
        </awm_tool_call>

        - Reading that call: name = "call_tool", arguments.tool_name = "create_applicant", arguments.arguments = the STRING "{{\\\"name\\\": \\\"Alice\\\", \\\"age\\\": 30}}".
        - A tool that takes no arguments: {{"name": "call_tool", "arguments": {{"tool_name": "list_camps", "arguments": "{{}}"}}}}.
        - Do NOT fall back to any native/DSML function-calling syntax in this step: the harness REJECTS the whole message (the environment never sees it) and replies with a FORMAT ERROR, which only wastes your steps.


        STRICT FORMAT REQUIREMENT (CRITICAL — read carefully):
        - This is a PLAIN-TEXT environment: there is NO native function-calling API. You simulate tool calls by outputting the "<awm_tool_call>...</awm_tool_call>" tag yourself.
        - Your tool call MUST be exactly the "<awm_tool_call>{{"name": ..., "arguments": ...}}</awm_tool_call>" tag shown above. Any OTHER syntax (DSML markers, "<final_tool_call>", "<invoke>"/"<parameter>" tags, or a leading "response" text) will NOT be recognized as a tool call.
        - If your step is not exactly "<awm_tool_call>...</awm_tool_call>" or "<final_answer>...</final_answer>", it will be treated as the end of the task and you will FAIL.
        - Emit ONLY ONE tag per step, and nothing else (no surrounding prose, no markdown fences).

        If the task only changes the database and requires no information to be returned, use exactly:
        <final_answer>This task requires no return value. I have completed the task.</final_answer>

        Stopping rule (IMPORTANT):
        - You have at most {max_steps} steps to complete the task. If you cannot finish within {max_steps} steps, it will be counted as a failure, so work efficiently and avoid wasting steps.
        - <final_answer> also consumes one step.

        Example #1 (list tools):
        <awm_tool_call>
        {{"name": "list_tools", "arguments": null}}
        </awm_tool_call>

        Example #2 (query tool detail):
        <awm_tool_call>
        {{"name": "get_tool_detail_of", "arguments": {{"tool_name": "get_weather"}}}}
        </awm_tool_call>

        Example #3 (call tool):
        <awm_tool_call>
        {{"name": "call_tool", "arguments": {{"tool_name": "get_weather", "arguments": "{{\\\"city\\\": \\\"Beijing\\\"}}"}}}}
        </awm_tool_call>""")


class RolloutState:
    """单条 rollout 的状态（批量 rollout 用）：对话历史 + 工具调用记录 + 是否结束。"""

    def __init__(self, system_prompt: str, task: str, env):
        self.env = env
        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        self.done = False                 # 是否已结束（final_answer / 无标签 / 超步数）
        self.prompt_ids_list = []         # 每步的 prompt token（供 log_prob 计算）
        self.completion_ids_list = []     # 每步新生成的 token（供 log_prob 计算）
        self.tool_call_names = []         # 工具调用顺序 [(外层工具名, 内层工具名), ...]
        self.has_5xx = False              # 是否出现过 env 5xx（环境代码 bug）
        self.error_details = []           # 5xx 详情 [(tool_name, result), ...]，供 bad_tasks 记录具体报错
        self.tools_cache = None           # list_tools 缓存的业务工具列表
        self.prompt_len = 0               # 当前 step 的真实 prompt 长度（截取 completion 用）
        self.successful_calls = 0         # 成功的业务工具调用数（返回非错误或 404）
        self.total_calls = 0              # 业务工具调用总数（call_tool 次数）


def _is_successful_call(result: str) -> bool:
    """判断一次 call_tool 是否成功。

    规则（按用户拍板）：
      - 正常返回（无 "Error:" 前缀）→ 成功；
      - 404（查不到数据，工具正常执行了）→ 成功；
      - 其他错误（5xx 环境 bug / 参数错 / 未知工具）→ 失败。
    """
    if "Error:" not in result:
        return True
    if "Status code: 404" in result:
        return True
    return False


def _apply_assistant_text(state: RolloutState, text: str, verbose: bool = False) -> str:
    """多标签解析：处理一次 assistant 回复（可能含 thinking + tool_call + final_answer）。

    优先级：
      1. 有 <awm_tool_call> → 执行所有工具，继续下一步（thinking/final_answer 都忽略，因为还要调工具）
      2. 无 tool_call 但有 <final_answer> → 结束
      3. 无 tool_call 无 final_answer 但有 <thinking> → 回复 "continue" 继续
      4. 什么都没有 → 兜底结束
    返回 "done" / "continue" / "executed"。
    verbose=True 时打印每个工具调用的名称/参数和环境回复（诊断用）。
    """
    tool_calls = parse_tool_calls(text)

    if tool_calls:
        for tc in tool_calls:
            name = tc["name"]
            arguments = tc["arguments"]
            if verbose:
                logger.info(f"    [工具调用] {name}，参数={arguments}")
            if name == "list_tools":
                state.tools_cache = state.env.list_tools()
                logger.info(f"[rollout] list_tools returned {len(state.tools_cache or [])} business tools")
                result = format_tools_for_response(state.tools_cache)
                state.tool_call_names.append(("list_tools", None))
            elif name == "get_tool_detail_of":
                tool_name = (arguments or {}).get("tool_name", "") if isinstance(arguments, dict) else ""
                tool = find_tool_by_name(state.tools_cache, tool_name)
                result = format_tool_detail(tool) if tool else f"Error: tool '{tool_name}' not found. Call list_tools first."
                # 去掉 mcp_tool_ 前缀，和 call_tool 的记录对齐（check_tool_order 要求 get_tool_detail_of 与 call_tool 的工具名一致）
                normalized = tool_name[len("mcp_tool_"):] if tool_name.startswith("mcp_tool_") else tool_name
                state.tool_call_names.append(("get_tool_detail_of", normalized))
            elif name == "call_tool":
                tool_name, inner_args = parse_call_tool_arguments(arguments)
                result = state.env.call_tool(tool_name, inner_args)
                if "Status code: 5" in result or "Internal Server Error" in result:
                    state.has_5xx = True
                    state.error_details.append((tool_name, result))  # 记录具体报错 + endpoint，供 bad_tasks 定点
                    # 打印 5xx 详情：工具名 + agent 传的参数 + 具体异常，便于判断是环境 bug 还是 agent 触发
                    logger.warning(f"[{state.env.scenario}] 工具 {tool_name} 触发 5xx：args={inner_args}，result={result[:400]}")
                state.tool_call_names.append(("call_tool", tool_name))
                # 统计业务工具调用：成功（非错误或 404）/ 失败（5xx、参数错等）
                state.total_calls += 1
                if _is_successful_call(result):
                    state.successful_calls += 1
            else:
                result = f"Error: unknown tool '{name}'"
            if verbose:
                # 环境回复（list_tools/get_tool_detail_of 的回复可能很长，截断）
                logger.info(f"    [环境回复] {result[:600]}")
            state.messages.append({"role": "user", "content": f"Tool response:\n{result}"})
        return "executed"

    if "<final_answer>" in text:
        return "done"

    if "<think" in text:  # 兼容 <think>（Qwen3 原生思考标签）和 <thinking>（我们的协议，以 <think 开头）
        state.messages.append({"role": "user", "content": "continue"})
        return "continue"

    return "done"


def _pad_batch(prompts: list, pad_token_id: int, device: str):
    """把不等长的 prompt 序列 left-pad 到同长（decoder-only 需要左侧 padding），返回 (batch_ids, attention_mask)。

    left-padding：真实 token 右对齐、pad 在左边，这样生成位置（最右）紧跟内容，
    注意力能正确看到历史；right-padding 会让 pad 插在内容与生成位置之间，生成错乱。
    """
    max_len = max(p.shape[0] for p in prompts)
    batch_ids = torch.full((len(prompts), max_len), pad_token_id, dtype=torch.long, device=device)
    attn_mask = torch.zeros((len(prompts), max_len), dtype=torch.long, device=device)
    for i, p in enumerate(prompts):
        batch_ids[i, max_len - p.shape[0]:] = p  # 真实 token 右对齐，pad 在左边
        attn_mask[i, max_len - p.shape[0]:] = 1
    return batch_ids, attn_mask


def _build_prompt_ids(tokenizer, messages) -> torch.Tensor:
    """把 messages 转成 1D prompt token（处理新旧 transformers 返回差异）。

    传 enable_thinking=False 关闭 Qwen3 的「思考模式」——否则模型每步在 <think> 里长篇思考，
    占满 max_new_tokens 导致 tool_call 的 arguments 截断（JSON 不完整、解析失败）。
    若 tokenizer 不支持该参数则回退到默认（此时靠 _apply_assistant_text 兼容 <think>）。
    """
    try:
        pid = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                            return_tensors="pt", enable_thinking=False)
    except Exception:
        pid = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                            return_tensors="pt")
    if hasattr(pid, "input_ids"):
        pid = pid["input_ids"]
    return pid[0]


def _strip_padding(completion: torch.Tensor, pad_token_id: int) -> torch.Tensor:
    """去掉 batch generate 里 EOS 之后的 padding（短序列会被 pad 到 batch 内最长）。"""
    pad_positions = (completion == pad_token_id).nonzero(as_tuple=True)[0]
    if len(pad_positions) > 0:
        return completion[: pad_positions[0].item()]
    return completion


def rollout_batch(model, tokenizer, task, envs, max_steps, temperature, max_new_tokens=1024, device="cuda", verbose=False, max_tokens_per_batch=9000):
    """批量 rollout：group_size 条轨迹同步推进 step，每个 step batch generate。

    返回 list[traj_dict]（与 envs 顺序对应）。每条 traj 含 prompt_ids / completion_ids /
    messages / has_5xx / tool_call_names（供 GRPO 更新与 reward 判分）。
    verbose=True 时打印每步的 LLM 输出和工具调用/环境回复（诊断用）。
    """
    system_prompt = _get_system_prompt(max_steps)
    states = [RolloutState(system_prompt, task, env) for env in envs]
    pad_token_id = tokenizer.pad_token_id or tokenizer.eos_token_id

    for step in range(max_steps):
        check_cancelled()
        active = [s for s in states if not s.done]
        if not active:
            break

        # 每条活跃 rollout 构建当前 prompt（tokenize，不 pad）
        prompts = []
        for s in active:
            prompts.append(_build_prompt_ids(tokenizer, s.messages))

        max_len = max(p.shape[0] for p in prompts)  # 最长 prompt 长度（left-pad 后，生成从 max_len 开始）
        if verbose:
            # 监控上下文长度：Qwen3-4B 上限 32768 tokens，这里打印当前最长 prompt 的 token 数
            logger.info(f"    [prompt 长度] 当前最长 prompt = {max_len} tokens（各轨迹 prompt 长度={[p.shape[0] for p in prompts]}）")

        # 显存预算：batch generate 的显存 ≈ batch × max_len × 常数（KV cache + prefill logits + 激活）。
        # 当 batch × max_len 超过 max_tokens_per_batch 时，拆成 sub-batch 依次 generate，
        # 把峰值显存锁死在预算内，避免 The Home Depot 这类超长 prompt 场景的 OOM。
        sub_batch_size = max(1, max_tokens_per_batch // max(1, max_len))
        sub_batch_size = min(sub_batch_size, len(active))

        for start in range(0, len(active), sub_batch_size):
            check_cancelled()
            sub_active = active[start:start + sub_batch_size]
            sub_prompts = prompts[start:start + sub_batch_size]
            sub_max_len = max(p.shape[0] for p in sub_prompts)
            batch_ids, attn_mask = _pad_batch(sub_prompts, pad_token_id, device)

            with torch.no_grad():
                outputs = model.generate(
                    batch_ids,
                    attention_mask=attn_mask,
                    max_new_tokens=max_new_tokens,
                    do_sample=True,
                    temperature=temperature,
                    pad_token_id=pad_token_id,
                    stopping_criteria=StoppingCriteriaList([_InterruptOnCancel()]),
                )

            # 逐条分发：截取新生成 token、解码、解析推进
            for j, s in enumerate(sub_active):
                out = outputs[j]
                # left-padding：prompt 右对齐到 sub_max_len，生成从 sub_max_len 开始
                completion = _strip_padding(out[sub_max_len:], pad_token_id)
                if completion.numel() == 0:
                    # 模型一上来就输出 EOS（生成为空）：标记结束，不记录这个空 step，
                    # 否则 compute_log_prob 里 torch.tensor([]) 会是 float 导致索引报错
                    s.done = True
                    continue
                text = tokenizer.decode(completion, skip_special_tokens=True)
                if verbose:
                    logger.info(f"===== Step {step} 轨迹 {start + j} LLM 输出 =====\n{text}")
                s.prompt_ids_list.append(sub_prompts[j].cpu().tolist())
                s.completion_ids_list.append(completion.cpu().tolist())
                s.messages.append({"role": "assistant", "content": text})
                action = _apply_assistant_text(s, text, verbose=verbose)
                if action == "done":
                    s.done = True

    return [
        {
            "prompt_ids": s.prompt_ids_list,
            "completion_ids": s.completion_ids_list,
            "messages": s.messages,
            "has_5xx": s.has_5xx,
            "error_details": s.error_details,
            "tool_call_names": s.tool_call_names,
            "successful_calls": s.successful_calls,
            "total_calls": s.total_calls,
        }
        for s in states
    ]


def rollout(model, tokenizer, task, env, max_steps, temperature, max_new_tokens=1024, device="cuda"):
    """单条 rollout（eval 用）：一条轨迹独立跑完 max_steps 步。

    与 rollout_batch 共用同一套多标签解析（_apply_assistant_text），只是 generate 是单条。
    """
    system_prompt = _get_system_prompt(max_steps)
    state = RolloutState(system_prompt, task, env)
    pad_token_id = tokenizer.pad_token_id or tokenizer.eos_token_id

    for step in range(max_steps):
        check_cancelled()
        if state.done:
            break
        pid = _build_prompt_ids(tokenizer, state.messages)
        state.prompt_len = pid.shape[0]
        prompt_ids = pid.unsqueeze(0).to(device)

        with torch.no_grad():
            outputs = model.generate(
                prompt_ids,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=temperature,
                pad_token_id=pad_token_id,
                stopping_criteria=StoppingCriteriaList([_InterruptOnCancel()]),
            )

        completion = outputs[0][state.prompt_len:]
        text = tokenizer.decode(completion, skip_special_tokens=True)
        state.prompt_ids_list.append(pid.cpu().tolist())
        state.completion_ids_list.append(completion.cpu().tolist())
        state.messages.append({"role": "assistant", "content": text})
        action = _apply_assistant_text(state, text)
        if action == "done":
            state.done = True

    return {
        "prompt_ids": state.prompt_ids_list,
        "completion_ids": state.completion_ids_list,
        "messages": state.messages,
        "has_5xx": state.has_5xx,
        "error_details": state.error_details,
        "tool_call_names": state.tool_call_names,
        "successful_calls": state.successful_calls,
        "total_calls": state.total_calls,
    }
