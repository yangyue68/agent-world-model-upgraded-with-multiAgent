"""Configuration for the task-level, single-file environment stage."""
from dataclasses import dataclass, field
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    input_spec: str = str(ROOT / "output/specific/gen_spec_ma.jsonl")
    input_task: str = str(ROOT / "output/task/gen_tasks_ma.jsonl")
    input_db: str = str(ROOT / "output/db/gen_db_ma.jsonl")
    input_sample: str = str(ROOT / "output/sample/gen_sample_ma.jsonl")
    output: str = str(ROOT / "output/environment/gen_envs_ma.jsonl")
    bad_output: str = str(ROOT / "output/environment/env_generate_bad.jsonl")
    envs_dir: str = str(ROOT / "output/environment/envs")
    trajectory_dir: str = str(ROOT / "output/environment/trajectory")
    model: str = "your-llm-model-name"
    probe_model: str = "your-llm-model-name"
    max_concurrency: int = 1
    request_timeout: float = 1200.0
    runtime_timeout: float = 90.0
    request_retry: int = 1
    max_fix_retry: int = 3
    rollout_count: int = 1
    max_probe_retry: int = 1
    max_steps: int = 12
    max_tokens: int = 24000
    probe_temperature: float = 0.0
    resume: bool = False
    limit: int | None = None
    allowed_scenarios: list[str] | None = field(default=None)
    verbose: bool = False
    save_raw_responses: bool = True

    def pre_process(self):
        self.model = os.environ.get("AWM_SYN_OVERRIDE_MODEL", self.model)
        self.probe_model = os.environ.get("AWM_SYN_OVERRIDE_MODEL", self.probe_model)
        for name in ("input_spec", "input_task", "input_db", "input_sample", "output", "bad_output", "envs_dir", "trajectory_dir"):
            value = Path(getattr(self, name))
            if not value.is_absolute():
                value = ROOT / value
            setattr(self, name, str(value.resolve()))
        for name in ("output", "bad_output", "envs_dir", "trajectory_dir"):
            Path(getattr(self, name)).parent.mkdir(parents=True, exist_ok=True)
        Path(self.envs_dir).mkdir(parents=True, exist_ok=True)
        Path(self.trajectory_dir).mkdir(parents=True, exist_ok=True)
        for name in ("input_spec", "input_task", "input_db", "input_sample"):
            if not Path(getattr(self, name)).is_file():
                raise FileNotFoundError(getattr(self, name))
        if not self.model or self.model == "your-llm-model-name":
            raise ValueError("请设置 --model 或 AWM_SYN_OVERRIDE_MODEL")
        if self.max_concurrency < 1 or self.max_fix_retry < 0 or self.rollout_count < 1:
            raise ValueError("并发数、修复次数和 rollout 次数必须有效")
        if self.max_tokens < 1 or self.max_steps < 1 or self.max_probe_retry < 0:
            raise ValueError("max_tokens、max_steps 和 max_probe_retry 必须为正数")
