# -*- coding: utf-8 -*-
"""正式训练前的一次性准备。"""

from dataclasses import dataclass, field

from loguru import logger
from torch.optim import AdamW

from lib.gpt import GPTClient
from lib.interrupt import check_cancelled
from stages.rl.checkpoint import build_bad_tasks_path
from stages.rl.config import Config, MA_ROOT
from stages.rl.data import DatasetBundle, fill_initial_snapshots, load_dataset
from stages.rl.model_setup import load_policy


@dataclass
class TrainingState:
    config: Config
    model: object
    tokenizer: object
    optimizer: object
    env_dir_map: dict
    train_items: list
    judge_client: object | None
    bad_tasks_path: str
    env_5xx_counter: dict = field(default_factory=dict)
    judge_error_counter: dict = field(default_factory=dict)
    bad_tasks: set = field(default_factory=set)
    baseline: float = 0.0
    baseline_n: int = 1
    stats: dict = field(default_factory=dict)


def prepare_training(config: Config) -> TrainingState:
    """加载模型、数据、judge、optimizer，并初始化训练状态。"""

    model, tokenizer = load_policy(config)
    check_cancelled()
    dataset: DatasetBundle = load_dataset(config, MA_ROOT)
    fill_initial_snapshots(dataset, MA_ROOT)
    check_cancelled()

    judge_client = None
    if config.judge_model:
        judge_client = GPTClient(provider=config.judge_provider or "azure", timeout=120)
        logger.info(f"LLM 裁判已启用：model={config.judge_model}")

    return TrainingState(
        config=config,
        model=model,
        tokenizer=tokenizer,
        optimizer=AdamW(model.parameters(), lr=config.learning_rate),
        env_dir_map=dataset.env_dir_map,
        train_items=dataset.train_items,
        judge_client=judge_client,
        bad_tasks_path=build_bad_tasks_path(config),
        baseline=config.baseline_prior,
        stats={
            "updated": 0,
            "skipped_zero_adv": 0,
            "skipped_few": 0,
            "bad_rollouts": 0,
            "bad_tasks": 0,
        },
    )
