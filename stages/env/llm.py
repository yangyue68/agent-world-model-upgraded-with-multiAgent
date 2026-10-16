"""env 专用的 LLM 调用和输出诊断，记录字符数、token 用量和结束原因。"""

import json
import time
from pathlib import Path

from loguru import logger

from lib.orchestrator import Agent
from lib.tools import safe_dir_name


class EnvAgent(Agent):
    def __init__(self, *args, diagnostics_dir=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostics_dir = diagnostics_dir
        self.last_finish_reason = None
        self.last_validation_error = ""

    def run(self, user_content: str) -> str:
        prefix = f"[{self.label}] " if self.label else ""
        self.last_validation_error = ""
        logger.info(f"{prefix}[Agent:{self.name}] running ...")
        response = self.client.batch_requests([self.build_request(user_content)], progress_bar=False)[0]
        choice = response.choices[0]
        raw = choice.message.content or ""
        self.last_finish_reason = getattr(choice, "finish_reason", None)
        usage = getattr(response, "usage", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        metadata = {
            "agent": self.name, "label": self.label, "model": self.model,
            "max_tokens": self.max_tokens, "chars": len(raw),
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": completion_tokens, "finish_reason": self.last_finish_reason,
        }
        logger.info(f"{prefix}[Agent:{self.name}] done ({len(raw)} chars, "
                    f"completion_tokens={completion_tokens}, finish_reason={self.last_finish_reason})")
        if self.diagnostics_dir:
            try:
                directory = Path(self.diagnostics_dir) / safe_dir_name(self.label or "unknown")
                directory.mkdir(parents=True, exist_ok=True)
                stem = f"{self.name}_{time.time_ns()}"
                (directory / f"{stem}.txt").write_text(raw, encoding="utf-8")
                (directory / f"{stem}.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                logger.warning(f"{prefix}原始输出诊断保存失败：{exc}")
        return raw


def decode_json_reply(raw):
    """去掉整个回复的 JSON 围栏，再严格解析，避免启发式 JSON 修复改坏源码。

    仅补 JSON 不允许的反斜杠单引号转义：保留源码中的反斜杠，补上它的 JSON
    转义层。缺引号、缺括号、混入解释等歧义输出直接报错，不猜测文件内容。
    """
    if not isinstance(raw, str):
        raise ValueError("回复必须是文本")
    text = raw.strip()
    lines = text.splitlines()
    if (len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"}
            and lines[-1].strip() == "```"):
        text = "\n".join(lines[1:-1]).strip()
    for _ in range(len(text) + 1):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # 模型把 Python 的 \' 直接写入 JSON 时，合法表示应是 \\'.
            # 使用解码器给出的精确位置，避免碰到其他已正确转义的内容。
            if exc.msg == "Invalid \\escape" and text[exc.pos:exc.pos + 2] == "\\'":
                text = text[:exc.pos] + "\\" + text[exc.pos:]
                continue
            raise
    raise ValueError("JSON 转义修复未收敛")


def parse_agent_output(agent, raw, scenario_name):
    """区分接口截断和 JSON 解析失败，不通过字符数猜测是否截断。"""
    if getattr(agent, "last_finish_reason", None) == "length":
        agent.last_validation_error = f"{agent.name} 输出被接口截断（finish_reason=length）"
        logger.warning(f"{scenario_name}: {agent.last_validation_error}")
        return None
    try:
        return decode_json_reply(raw)
    except (ValueError, TypeError) as exc:
        agent.last_validation_error = f"{agent.name} 输出不是可无损解析的 JSON：{exc}"
        logger.warning(f"{scenario_name}: {agent.last_validation_error}")
        return None
