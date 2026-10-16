"""模型请求、原文诊断与有限重试；格式失败不冒充业务失败。"""

import json
import re
import time
from pathlib import Path

from loguru import logger
from lib.gpt import GPTClient
from lib.interrupt import check_cancelled
from . import prompts


class ReplyError(ValueError):
    pass


def decode_reply(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("模型返回空 content")
    text = raw.strip()
    # 仅去掉完整思考段和完整外围围栏；不修补截断 JSON 或猜测缺失字段。
    text = re.sub(r"^<think>.*?</think>\s*", "", text, count=1, flags=re.S)
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


class SpecAgent:
    def __init__(self, name, system_prompt, config, label, directory):
        self.name, self.system_prompt = name, system_prompt
        self.config, self.label, self.directory = config, label, directory
        self.client = GPTClient(timeout=config.request_timeout)
        self.max_tokens = {"planner": config.plan_max_tokens,
                           "reviewer": config.review_max_tokens}.get(name, config.max_tokens)

    def run(self, prompt):
        check_cancelled()
        start = time.monotonic()
        logger.info(f"[{self.label}] {self.name} 开始，输入 {len(prompt)} chars")
        response = self.client.batch_requests([{
            "model": self.config.model, "temperature": 0.0 if self.name == "reviewer" else 0.3,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": self.system_prompt},
                         {"role": "user", "content": prompt}],
        }], progress_bar=False)[0]
        choice = response.choices[0]
        raw = choice.message.content or ""
        finish = getattr(choice, "finish_reason", None)
        tokens = getattr(getattr(response, "usage", None), "completion_tokens", None)
        logger.info(f"[{self.label}] {self.name} 返回 {len(raw)} chars，tokens={tokens}，"
                    f"finish={finish}，耗时 {time.monotonic() - start:.1f}s")
        if self.config.save_raw_responses:
            path = Path(self.directory) / f"{self.name}_{time.time_ns()}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.with_suffix(".txt").write_text(raw, encoding="utf-8")
            path.with_suffix(".json").write_text(json.dumps({
                "model": self.config.model, "finish_reason": finish,
                "completion_tokens": tokens, "chars": len(raw),
                "system_prompt": self.system_prompt, "prompt": prompt,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        if finish not in (None, "stop"):
            raise ReplyError(f"{self.name}: 非正常结束 finish_reason={finish}，不使用不完整响应")
        return raw


def build_agents(config, label, directory):
    return {name: SpecAgent(name, prompt, config, label, directory) for name, prompt in (
        ("planner", prompts.PLANNER), ("designer", prompts.DESIGNER),
        ("reviewer", prompts.REVIEWER), ("fixer", prompts.FIXER))}


def request_json(agent, prompt, parser, retries, label):
    error = ""
    for attempt in range(retries + 1):
        check_cancelled()
        try:
            retry_prompt = prompt
            if error:
                retry_prompt += "\nPrevious response failed: " + error + "\nReturn the complete requested JSON."
            return parser(decode_reply(agent.run(retry_prompt)))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning(f"[{label}] 响应失败 {attempt + 1}/{retries + 1}：{error}")
    raise ReplyError(f"{label} 请求/格式重试耗尽：{error}")
