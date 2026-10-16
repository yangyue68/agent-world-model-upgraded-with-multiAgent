# -*- coding: utf-8 -*-
"""公共「跑轨迹」能力：env 阶段和 verifier 阶段共用。

用 rl 的协议（两标签 + InProcessEnv 直调）跑真实 agent 轨迹，后端是 LLM API（deepseek 等）。
- env 阶段用它检测环境代码有没有 bug（no such column / 5xx / 隐性逻辑错）；
- verifier 阶段用它检测 verify 判据是否健康。

依赖 stages.rl.rollout 的协议函数（_get_system_prompt / RolloutState / _apply_assistant_text），
保证「跑轨迹」与 rl 训练/评估完全对齐。
"""

import re
import sys
from pathlib import Path

from loguru import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from lib.runtime import has_native_tool_protocol  # noqa: E402
from stages.rl.rollout import _get_system_prompt, RolloutState, _apply_assistant_text  # noqa: E402


# 模型违反协议（吐 DSML / invoke / parameter / final_tool_call 等原生 tool-calling 格式）时回的纠错消息。
# 关键点：明确告诉它「这次调用一步都没执行、环境根本没收到」，要求按约定协议原样重发。
# 使用普通文本 <awm_tool_call>，避免 Qwen3/vLLM 将旧标签当作特殊 token 吞掉。
FORMAT_CORRECTION_MESSAGE = (
    "[FORMAT ERROR] Your previous message did not follow the required protocol. "
    "This environment does NOT support any native tool-calling protocol: markers such as "
    '"<|DSML|invoke ...>", "<|DSML|parameter ...>", "<invoke>", "<parameter>", '
    '"<final_tool_call>" or "<function_call>" are NOT recognized. '
    "Your tool call was REJECTED — it was NOT sent to the environment, so nothing has happened yet.\n\n"
    "Re-send the SAME intended action now, using EXACTLY this syntax and nothing else:\n\n"
    '<awm_tool_call>{"name": "call_tool", "arguments": {"tool_name": "<tool name>", '
    '"arguments": "<the tool arguments as a JSON string>"}}</awm_tool_call>\n\n'
    "Notes:\n"
    '- The outer shape is always this plain-text tag. The inner "arguments" value is a JSON *string*, '
    'e.g. "{\\"city\\": \\"Beijing\\"}", or "{}" when the tool takes no arguments.\n'
    '- For the tool catalogue use: <awm_tool_call>{"name": "list_tools", "arguments": null}</awm_tool_call>\n'
    '- For a schema lookup use: <awm_tool_call>{"name": "get_tool_detail_of", '
    '"arguments": {"tool_name": "<tool name>"}}</awm_tool_call>\n'
    "- No markdown fences, no prose before or after the tag.\n"
    "- If you truly cannot continue, reply with ONLY <final_answer>...</final_answer>."
)

# 同一个 step 内最多纠错几次（纠错重发不消耗 step 预算，但要防模型死磕原生格式导致无限循环）
MAX_FORMAT_RETRY = 2

# ---- 严格模式（默认用于 env / verifier 探针，RL 阶段仍走宽松模式）----
# 判据：回复里必须出现约定的三种标签之一。
# 为什么 <thinking> / <final_answer> 也算「被接受」：它们是本协议自带的两类回合
# （rl/rollout.py 的 _apply_assistant_text 就是按这三个标签解析的），全驳回会让模型
# 既不能思考也不能收尾，轨迹直接死掉。「其余全驳回」拦的是原生 tool-calling 方言：
# 裸 JSON、<invoke>/<parameter> 块、to=functions.xxx、DSML 变体等 —— 这类文本只要不带
# DSML 字样，宽松模式的标记检测就抓不到，会被当普通文本静默放过（step 白烧、工具没执行）。
_STRICT_ALLOWED_TAGS = (
    re.compile(r"<(?:awm_tool_call|tool_call)\b", re.I),
    re.compile(r"<thinking\b", re.I),
    re.compile(r"<final_answer\b", re.I),
)

# 严格模式专用补充说明（追加在通用纠错消息后面，明确告诉模型「没标签的回复被丢弃了」）
STRICT_FORMAT_CORRECTION_HINT = (
    "\n\n[STRICT MODE] Your reply contained NONE of the accepted tags, so it was DISCARDED. "
    "Only these three forms are accepted here:\n"
    '- <awm_tool_call>{"name": ..., "arguments": ...}</awm_tool_call>\n'
    "- <thinking>...</thinking>\n"
    "- <final_answer>...</final_answer>\n"
    "Raw JSON, markdown code fences, `to=functions.xxx` style calls, <invoke>/<parameter> blocks "
    "and every other tool-calling dialect are invalid and are thrown away."
)


def check_protocol(text: str, strict: bool = False) -> str | None:
    """检查一次 assistant 回复是否违反协议，返回违规类型（None = 合规，可以交给解析器处理）。

    - 宽松模式（strict=False，RL 阶段默认）：只拦「用了 DSML 等原生 tool-calling 标记」的消息
      （标记表见 lib.runtime.has_native_tool_protocol）；
    - 严格模式（strict=True，env/verifier 探针默认）：在宽松模式基础上再加一条硬约束 ——
      回复里必须出现 <awm_tool_call> / <thinking> / <final_answer> 之一，否则一律驳回重发。
      这样「检测函数抓不到」的原生格式（裸 JSON、无 DSML 前缀的 <invoke> 等）也不会再被吃掉。
    """
    if has_native_tool_protocol(text):
        return "native_tool_protocol"
    if strict and not any(pattern.search(text) for pattern in _STRICT_ALLOWED_TAGS):
        return "no_accepted_tag"
    return None


def chat(client, messages, temperature, max_new_tokens, model=None):
    """用 GPTClient 同步调一次 LLM，返回文本（异常返回空串）。model=None 沿用 client 默认/环境变量模型。"""
    try:
        return client.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_new_tokens,
            model=model or "your-llm-model-name",
        ) or ""
    except Exception as e:
        logger.warning(f"[rollout] LLM 调用失败：{e}")
        return ""


def run_single_rollout(client, task, env, max_steps, temperature=1.0, max_new_tokens=2048, verbose=False,
                       model=None, max_format_retry: int = MAX_FORMAT_RETRY, strict_format: bool = False):
    """单条 rollout：复用 rl 的 RolloutState + 多标签解析，generate 换成 LLM API。

    额外加了一层「协议纠错」：模型如果用原生 tool-calling 协议（DSML / <invoke> / <parameter> /
    <final_tool_call>）而不是约定的 <awm_tool_call>，这次工具调用会被【直接驳回、绝不发给 env】，
    并回一条纠错消息要求它按约定协议原样重发（最多 max_format_retry 次，重发不额外消耗 step）。
    这样既不会拿模型的格式错误去误判环境代码，也不会让坏格式静默变成「0 次工具调用」。

    strict_format=True 时开「严格模式」：回复里必须出现 <awm_tool_call>/<thinking>/<final_answer>
    之一，否则同样驳回重发（拦掉检测函数抓不到的原生格式，如裸 JSON）。env 阶段的探针默认开启。

    返回结构与 rl.rollout.rollout 对齐（messages / has_5xx / error_details / tool_call_names / *calls），
    另加 format_violations（命中协议违规的次数）/ format_resends（实际发出的纠错消息条数）。
    没有 prompt_ids/completion_ids（纯文本后端，不需要 token 级 log_prob）。
    """
    system_prompt = _get_system_prompt(max_steps)
    state = RolloutState(system_prompt, task, env)
    try:
        advertised_tools = list(env.list_tools() or [])
    except Exception as exc:
        advertised_tools = []
        logger.warning(f"[rollout] failed to read environment tool catalogue: {exc}")
    logger.info(f"[rollout] environment tool catalogue contains {len(advertised_tools)} tools")
    format_violations = 0   # 命中协议违规的次数
    format_resends = 0      # 实际发出的纠错消息条数

    step = 0
    while step < max_steps and not state.done:
        text = ""
        # 同一个 step 内：命中协议违规就驳回 + 纠错重发，重发的这几轮不占用 step（免得坏格式吃掉步数预算）
        for retry in range(max_format_retry + 1):
            text = chat(client, state.messages, temperature, max_new_tokens, model)
            if not text:
                state.done = True
                break
            if verbose:
                logger.info(f"===== Step {step} LLM 输出 =====\n{text}")
            state.messages.append({"role": "assistant", "content": text})
            violation = check_protocol(text, strict=strict_format)
            if violation is None:
                break
            # ★ 违规：这条消息一步都不发给 env，只回一条纠错消息让模型按约定协议重发
            format_violations += 1
            if violation == "native_tool_protocol":
                logger.warning(
                    f"[rollout] step {step}: 模型用了原生 tool-calling 协议（DSML 等），"
                    f"已驳回该工具调用（未发给 env），要求按 <awm_tool_call> 协议重发（第 {format_violations} 次违规）"
                )
            else:
                logger.warning(
                    f"[rollout] step {step}: 模型没用 <awm_tool_call>/<thinking>/<final_answer> 中的任何一个标签"
                    f"（严格模式），已驳回该消息（未发给 env），要求按 <awm_tool_call> 协议重发"
                    f"（第 {format_violations} 次违规）"
                )
            if retry >= max_format_retry:
                logger.warning(f"[rollout] step {step}: 连续 {retry + 1} 次违规仍未改，提前结束该轨迹")
                state.done = True
                break
            format_resends += 1
            correction = FORMAT_CORRECTION_MESSAGE
            if violation == "no_accepted_tag":  # 严格模式专用补充：说清「没标签 = 被丢弃」
                correction += STRICT_FORMAT_CORRECTION_HINT
            state.messages.append({"role": "user", "content": correction})
        if state.done:
            break
        action = _apply_assistant_text(state, text, verbose=verbose)
        if action == "done":
            state.done = True
        step += 1

    if format_violations:
        logger.warning(f"[rollout] 本次轨迹命中 {format_violations} 次协议违规，发出 {format_resends} 条纠错消息")

    return {
        "messages": state.messages,
        "has_5xx": state.has_5xx,
        "error_details": state.error_details,
        "tool_call_names": state.tool_call_names,
        "successful_calls": state.successful_calls,
        "total_calls": state.total_calls,
        "format_violations": format_violations,
        "format_resends": format_resends,
        "tool_catalogue_count": len(advertised_tools),
    }
