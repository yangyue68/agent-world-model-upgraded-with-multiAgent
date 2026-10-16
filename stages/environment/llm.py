"""Small, diagnosable LLM adapters used by the environment pipeline."""
import json
import re
import time
from pathlib import Path
from loguru import logger
from lib.gpt import GPTClient
from lib.interrupt import check_cancelled
from lib.tools import tools_robust_json_loads
from . import prompts


class ReplyError(RuntimeError):
    pass


def _content(response):
    choice = response.choices[0]
    message = choice.message
    return (getattr(message, "content", None) or "").strip(), getattr(choice, "finish_reason", None), getattr(getattr(response, "usage", None), "completion_tokens", None)


def extract_code(raw):
    text = str(raw or "").strip()
    text = re.sub(r"^<think>.*?</think>\s*", "", text, count=1, flags=re.S)
    match = re.search(r"```(?:python|py)?\s*\n?(.*?)```", text, flags=re.S | re.I)
    if match:
        text = match.group(1).strip()
    if not text or "FastAPI" not in text or "app" not in text:
        raise ReplyError("模型没有返回完整 FastAPI main.py")
    return text


def parse_review(raw):
    data = tools_robust_json_loads(raw)
    if not isinstance(data, dict) or data.get("verdict") not in {"pass", "environment_bug", "upstream_conflict", "agent_failure", "inconclusive"}:
        raise ReplyError("review JSON 缺少有效 verdict")
    issues = data.get("issues", [])
    data["issues"] = [str(issue) for issue in issues] if isinstance(issues, list) else [str(issues)]
    return data


class Agent:
    def __init__(self, name, system_prompt, config, label, raw_dir):
        self.name, self.system_prompt, self.config, self.label = name, system_prompt, config, label
        self.raw_dir = Path(raw_dir)
        self.client = GPTClient(timeout=config.request_timeout)

    def run(self, user_prompt, parser):
        check_cancelled()
        started = time.monotonic()
        logger.info(f"[{self.label}] {self.name} start input_chars={len(user_prompt)}")
        response = self.client.batch_requests([{"model": self.config.model,
            "temperature": 0.0 if self.name in {"reviewer", "trajectory_reviewer"} else 0.2,
            "max_tokens": self.config.max_tokens,
            "messages": [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": user_prompt}]}], progress_bar=False)[0]
        raw, finish, tokens = _content(response)
        logger.info(f"[{self.label}] {self.name} response_chars={len(raw)} tokens={tokens} finish={finish} elapsed={time.monotonic()-started:.1f}s")
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        stamp = str(time.time_ns())
        (self.raw_dir / f"{self.name}_{stamp}.txt").write_text(raw, encoding="utf-8")
        if finish not in (None, "stop"):
            raise ReplyError(f"{self.name}: finish_reason={finish}")
        return parser(raw)


def build_agents(config, label, raw_dir):
    return {name: Agent(name, prompt, config, label, raw_dir) for name, prompt in (
        ("generator", prompts.GENERATOR), ("fixer", prompts.FIXER),
        ("reviewer", prompts.REVIEWER), ("trajectory_reviewer", prompts.TRAJECTORY_REVIEWER))}


def request(agent, user_prompt, parser, retries):
    error = ""
    for attempt in range(retries + 1):
        try:
            prompt = user_prompt + (f"\nPrevious response error: {error}\nReturn the complete requested output." if error else "")
            return agent.run(prompt, parser)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning(f"[{agent.label}] {agent.name} retry {attempt + 1}/{retries + 1}: {error}")
    raise ReplyError(f"{agent.name} retries exhausted: {error}")
