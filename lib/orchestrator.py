"""
通用多 agent 编排层（与具体业务步骤无关）。

用途：把原来"一次 GPTClient 调用生成全部内容"的单次生成(这句话实际上不准确，原来项目里的gpt.py里是把1000个请求并发地发送给LLM的，
     具体方法是把提起1000个线程，但是限制并发数为64，对于LLM来说，它一次只看见一个场景的请求，并只返回一个response，而不是一次生成1000个场景的
     task一次性返回）
     升级为"多角色协作 + 4 种工作流模式"的多 agent 生成。

本文件不依赖任何具体步骤（scenario/task/db/env...），7 个步骤全部复用。

约定：
  - Agent 之间通过 JSON 字符串交换结构化数据。
  - Orchestrator 只负责"怎么串"，不关心"生成的是什么"。
"""

from __future__ import annotations  # 让类型注解里的 str | None、dict 等写法在旧版本也合法

import json  # 用于把反馈意见序列化成 JSON 字符串
from typing import Any, Callable, List  # 类型注解用

from loguru import logger  # 日志工具，打印带颜色、分级别的运行信息

from lib.gpt import GPTClient  # 复用项目自带的 LLM 客户端（支持并发、重试）
from lib.tools import tools_robust_json_loads  # 复用稳健 JSON 解析（内部用 json_repair）


class Agent:
    """单个智能体 = 角色(system_prompt) + 一次 LLM 调用。

    它是"多 agent"的最小单元：一个 Agent 就是一个有明确职责、提示词固定的角色。
    """

    def __init__(
        self,
        name: str,                        # agent 名字，仅用于日志打印，方便定位是哪个角色
        system_prompt: str,               # 该角色的系统提示词，决定它的行为/职责边界
        client: GPTClient | None = None,  # LLM 客户端；不传则默认新建一个
        model: str | None = None,         # 模型名；不传则沿用 GPTClient 的环境变量覆盖
        temperature: float = 0.7,         # 采样温度：生成类用高值（多样），评审类用 0（稳定）
        max_tokens: int = 16384,          # 单次最大输出 token 数
        label: str | None = None,         # 可选的场景名/标签，日志里带上以区分是哪个场景
    ):
        self.name = name
        self.system_prompt = system_prompt
        # 默认每个 Agent 独立建一个 GPTClient；也可传入共享实例以复用同一个并发信号量
        self.client = client or GPTClient(timeout=120)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.label = label

    def build_request(self, user_content: str) -> dict:
        """把一个用户提示包装成一次 LLM 请求（供 run 和 map_reduce 复用，避免重复拼装）。"""
        req = {
            "messages": [
                {"role": "system", "content": self.system_prompt},  # 系统角色：定职责
                {"role": "user", "content": user_content},          # 用户角色：具体任务
            ],
            "temperature": self.temperature,   # 采样温度
            "max_tokens": self.max_tokens,     # 最大输出长度
        }
        # model 只在显式传入时才写进请求，避免覆盖 GPTClient 里从环境变量读到的模型名
        if self.model is not None:
            req["model"] = self.model
        return req

    def run(self, user_content: str) -> str:
        """同步调用，返回纯文本回复。"""
        prefix = f"[{self.label}] " if self.label else ""
        logger.info(f"{prefix}[Agent:{self.name}] running ...")
        # 复用 GPTClient 的批量接口，但这里只发 1 条请求，取回第 0 条结果即可
        resp = self.client.batch_chat_completion(
            [self.build_request(user_content)], progress_bar=False
        )[0]
        logger.info(f"{prefix}[Agent:{self.name}] done ({len(resp)} chars)")
        return resp

    def run_json(self, user_content: Any) -> dict:
        """把输入序列化为 JSON 调用，再把回复解析成 dict。"""
        # 如果传入的不是字符串（比如 dict/list），先转成 JSON 字符串再喂给模型
        if not isinstance(user_content, str):
            user_content = json.dumps(user_content, ensure_ascii=False, default=str)
        # 调用模型后用稳健解析器把回复转成 dict（容忍 markdown 代码块等噪声）
        return tools_robust_json_loads(self.run(user_content))


class Orchestrator:
    """4 种工作流模式，与具体业务无关。

    它只负责"把 agent 按什么顺序/规则串起来"，不关心 agent 具体在生成场景还是代码。
    """

    def chain(self, agents: List[Agent], initial_input: str) -> str:
        """模式① 顺序链：A→B→C，上一个 agent 的输出直接作为下一个的输入。"""
        out = initial_input           # 初始输入
        for agent in agents:          # 按顺序依次执行每个 agent
            out = agent.run(out)      # 上一个的输出作为下一个的输入
        return out                    # 返回最后一个 agent 的输出

    def review_loop(
        self,
        generator: Agent,       # 生成者 agent
        reviewer: Agent,        # 评审者 agent（需输出 {"pass": bool, "issues": [...]}）
        initial_input: str,     # 初始提示词
        max_rounds: int = 3,    # 最多生成+评审几轮
    ) -> tuple[str, dict]:
        """模式② 评审循环：生成 → 评审 → 不通过则带意见重生成。

        返回 (最终产物文本, 最终评审结果)。
        """
        review: dict = {}        # 存评审结果
        text = ""                # 存生成文本
        feedback = ""            # 存"上一轮评审意见"，首轮为空字符串
        for _ in range(max_rounds):
            # 把上一轮的评审意见拼在提示词后面，让生成者"带着意见重写"
            text = generator.run(initial_input + feedback)
            try:
                # 评审者直接看生成文本，返回 {"pass": bool, "issues": [...]}
                review = reviewer.run_json(text)
                # 防御：评审者若返回了非 dict（如 list），当失败处理
                if not isinstance(review, dict):
                    review = {"pass": False, "issues": ["评审输出不是有效的 JSON 对象"]}
            except Exception as e:
                # 防御：评审解析失败（返回非 JSON 文本）也不让整个流程崩溃
                review = {"pass": False, "issues": [f"评审解析失败: {e}"]}
            if review.get("pass"):
                return text, review   # 通过 → 返回最终产物和评审结果
            # 未通过 → 把问题列表拼成反馈，供下一轮生成者参考
            feedback = (
                "\n\n【上一次评审未通过，请按以下意见修正】\n"
                + json.dumps(review.get("issues", []), ensure_ascii=False)
            )
        # 达到最大轮次仍未通过，返回最后一轮的结果（交给上层决定怎么兜底）
        logger.warning(f"[Orchestrator:review_loop] 达到最大轮次 {max_rounds}，返回最后结果")
        return text, review

    def map_reduce(
        self,
        items: List[Any],                        # 要处理的一批任务
        worker: Agent,                           # 干活的 agent（同一个角色）
        to_user_content: Callable[[Any], str],   # map：把每个 item 转成用户提示的函数
        merger: Callable[[List[str]], Any] | None = None,  # reduce：把 N 个结果合并成 1 个
    ) -> Any:
        """模式③ 并行 map-reduce：map 阶段把 items 拆开并行处理，reduce 阶段把结果合并。

        map 阶段：为每个 item 构造请求，交给 GPTClient 批量并发（底层就是 gpt.py 的并发）。
        reduce 阶段：用 merger 把 N 个并行结果合并成一个整体。

        merger 是"合并函数"：输入是"每个 item 对应的生成文本列表"，输出是合并后的结果。
        不传 merger 则退化为纯并发 map（直接返回结果列表）。
        """
        # map 阶段：为每个 item 构造请求，交给 GPTClient 批量并发
        requests = [worker.build_request(to_user_content(item)) for item in items]
        results = worker.client.batch_chat_completion(requests, progress_bar=True)
        # reduce 阶段：合并结果（merger 为空则退化为纯 map）
        if merger is None:
            return results
        return merger(results)

    def exec_loop(
        self,
        generator: Agent,                    # 首次生成者
        executor: Callable[[str], dict],     # 真实执行器（如"起服务器"），返回 {"success": bool, "error": str}
        fixer: Agent,                        # 修复者（拿到报错后定向修复，而不是整段重写）
        gen_input: str,                      # 初始提示
        max_rounds: int = 3,                 # 最多修复几轮
    ) -> tuple[str, dict]:
        """模式④ 执行循环：生成 → 真实执行 → 报错则定向修复 → 再执行。

        返回 (最终产物文本, 最终执行状态)。
        """
        text = generator.run(gen_input)      # 先首次生成
        state = executor(text)               # 立刻真实执行，拿到成功/失败状态
        for _ in range(max_rounds):
            if state.get("success"):
                return text, state           # 执行成功 → 返回
            # 失败 → 把"上次结果 + 报错"交给 fixer 定向修复（而不是让 generator 重写）
            text = fixer.run(
                "上次生成结果：\n"
                + text
                + "\n\n执行报错信息：\n"
                + str(state.get("error", ""))
            )
            state = executor(text)           # 修复后再执行一次
        logger.warning(f"[Orchestrator:exec_loop] 达到最大轮次 {max_rounds}，返回最后结果")
        return text, state
