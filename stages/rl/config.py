# -*- coding: utf-8 -*-
"""GRPO 训练配置。"""

from dataclasses import dataclass
from pathlib import Path


MA_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class Config:
    """RL 训练配置。"""

    # ---- 模型 ----
    model_name: str = "Qwen/Qwen3-4B"  # 基础模型（原论文 Arctic-AWM-4B 基于 Qwen3-4B）
    use_modelscope: bool = True  # True 时从魔搭下载；False 走 HuggingFace/本地路径

    # ---- LoRA ----
    use_lora: bool = True
    lora_r: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05

    # ---- 环境产物 ----
    tasks_path: str = str(MA_ROOT / "output" / "task" / "gen_tasks_ma.jsonl")
    envs_path: str = str(MA_ROOT / "output" / "environment" / "gen_envs_ma.jsonl")
    db_path: str = str(MA_ROOT / "output" / "db" / "gen_db_ma.jsonl")
    sample_path: str = str(MA_ROOT / "output" / "sample" / "gen_sample_ma.jsonl")
    verifier_path: str = str(MA_ROOT / "output" / "verifier" / "gen_verifier_ma.jsonl")  # 兼容旧命令；RL 不读取其中 verifier 代码
    output_dir: str = str(MA_ROOT / "stages" / "rl" / "output")

    # ---- 训练超参 ----
    num_epochs: int = 3
    group_size: int = 10
    max_steps: int = 15
    temperature: float = 1.0
    learning_rate: float = 1e-3
    max_new_tokens: int = 30000
    max_tokens_per_batch: int = 80000
    # 被训练 policy 是否启用 Qwen3 thinking；默认关闭以减少 rollout 成本。
    enable_thinking: bool = False
    device: str = "cuda"
    env_start_retries: int = 3
    verbose: bool = False
    logsumexp_chunk_size: int = 150000

    # ---- 数据范围 ----
    limit_scenarios: int | None = None
    max_tasks: int | None = None
    allowed_scenarios_path: str | None = None
    allowed_tasks_path: str | None = None
    start_task: int = 0
    end_task: int | None = None

    # ---- checkpoint / 增量训练 ----
    checkpoint_every: int = 5
    load_adapter: str | None = None

    # ---- 坏产物检测 ----
    bad_env_threshold: int = 3
    bad_verify_threshold: int = 3

    # ---- advantage baseline ----
    baseline_eps: float = 1e-2
    baseline_prior: float = 0.6

    # ---- LLM 裁判 ----
    judge_model: str | None = None
    judge_provider: str = ""
