"""独立新版 spec 配置；ma_upgrade 即项目根目录。"""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    input_task: str = str(ROOT / "output/task/gen_tasks_ma.jsonl")
    input_db: str = str(ROOT / "output/db/gen_db_ma.jsonl")
    input_sample: str = str(ROOT / "output/sample/gen_sample_ma.jsonl")
    output: str = str(ROOT / "output/specific/gen_spec_ma.jsonl")
    bad_output: str = str(ROOT / "output/specific/spec_bad_tasks.jsonl")
    model: str = "your-llm-model-name"
    allowed_scenarios: list[str] | None = None
    limit: int | None = None
    max_concurrency: int = 4
    max_retry: int = 3  # 修复次数，不含首次生成；最后一次修复仍须完整检查。
    plan_retry: int = 2
    generation_retry: int = 1  # 请求/JSON/外层结构失败的额外重试。
    review_retry: int = 1
    max_tokens: int = 50000
    plan_max_tokens: int = 50000
    review_max_tokens: int = 50000
    request_timeout: float = 1200.0
    sample_per_table: int = 2
    resume: bool = True
    save_raw_responses: bool = False  # 生产环境关闭，调试时开

    def pre_process(self):
        for name in ("input_task", "input_db", "input_sample"):
            if not Path(getattr(self, name)).is_file():
                raise ValueError(f"{name} 文件不存在：{getattr(self, name)}")
        paths = [Path(getattr(self, n)).resolve() for n in
                 ("input_task", "input_db", "input_sample", "output", "bad_output")]
        if len(set(paths)) != len(paths):
            raise ValueError("输入、成功输出和失败输出必须使用不同文件")
        for name in ("output", "bad_output"):
            if Path(getattr(self, name)).suffix != ".jsonl":
                raise ValueError(f"{name} 必须为 .jsonl")
        self.model = os.environ.get("AWM_SYN_OVERRIDE_MODEL") or self.model
        if not self.model or self.model == "your-llm-model-name":
            raise ValueError("请设置 --model 或 AWM_SYN_OVERRIDE_MODEL")
        for name in ("max_retry", "plan_retry", "generation_retry", "review_retry", "sample_per_table", "limit"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} 不能为负数")
        for name in ("max_concurrency", "max_tokens", "plan_max_tokens", "review_max_tokens", "request_timeout"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须大于 0")
