# -*- coding: utf-8 -*-
"""加载 env/task/sample 产物并构造 RL 训练样本。"""

import os
from dataclasses import dataclass

from loguru import logger

from lib.db_diff import snapshot_db
from lib.tools import normalize_scenario_name, resolve_project_path, tools_jsonl_load
from stages.rl.config import Config


@dataclass
class TrainItem:
    scenario: str
    task_id: int
    task: dict
    verifier: dict


@dataclass
class DatasetBundle:
    env_dir_map: dict[tuple[str, int], str]
    train_items: list[TrainItem]


def load_dataset(config: Config, ma_root) -> DatasetBundle:
    """只保留 env 阶段有环境、sample 阶段有样本的任务。"""

    env_dir_map = _load_env_dirs(config, ma_root)
    scenario_set = {scenario for scenario, _ in env_dir_map}
    tasks_data = tools_jsonl_load(config.tasks_path)
    sample_data = tools_jsonl_load(config.sample_path)
    sample_map = {
        (normalize_scenario_name(item.get("scenario", "")), item.get("task_id", "")): item
        for item in sample_data
    }

    scenario_names = _select_scenarios(config, tasks_data, scenario_set)
    selected_scenarios = set(scenario_names) & scenario_set
    scenarios = [
        entry for entry in tasks_data
        if normalize_scenario_name(entry["scenario"]) in selected_scenarios
    ]
    allowed_tasks = _load_allowed_tasks(config)

    train_items = []
    for entry in scenarios:
        scenario = normalize_scenario_name(entry["scenario"])
        for task_id, task in enumerate(entry.get("tasks", [])):
            if config.max_tasks is not None and task_id >= config.max_tasks:
                break
            if allowed_tasks is not None and (scenario, task_id) not in allowed_tasks:
                continue
            verifier = sample_map.get((scenario, f"task_{task_id + 1}"))
            if verifier is not None:
                train_items.append(TrainItem(scenario, task_id, task, verifier))

    if config.start_task or config.end_task is not None:
        train_items = train_items[config.start_task:config.end_task]

    logger.info(f"筛选到 {len(scenarios)} 个场景、{len(train_items)} 个训练任务")
    return DatasetBundle(env_dir_map=env_dir_map, train_items=train_items)


def fill_initial_snapshots(bundle: DatasetBundle, ma_root) -> None:
    """为旧 sample 产物补齐 initial_snapshot，避免训练循环重复读取数据库。"""

    for item in bundle.train_items:
        if item.verifier.get("initial_snapshot") is not None:
            continue
        source = item.verifier.get("db_path", "")
        if source:
            source = str(resolve_project_path(source, ma_root))
        if source and os.path.exists(source):
            item.verifier["initial_snapshot"] = snapshot_db(source)


def _load_env_dirs(config: Config, ma_root) -> dict[tuple[str, int], str]:
    env_dir_map = {}
    for entry in tools_jsonl_load(config.envs_path):
        scenario = normalize_scenario_name(entry.get("scenario", ""))
        env_dir = entry.get("env_dir")
        if not env_dir:
            continue
        env_dir = str(resolve_project_path(env_dir, ma_root))
        task_id = entry.get("task_id", "")
        try:
            task_index = int(task_id.split("_", 1)[1]) - 1
        except (ValueError, IndexError):
            task_index = 0
        env_dir_map[(scenario, task_index)] = env_dir
    return env_dir_map


def _select_scenarios(config: Config, tasks_data: list[dict], scenario_set: set[str]) -> list[str]:
    if config.allowed_scenarios_path:
        entries = tools_jsonl_load(config.allowed_scenarios_path)
        names = [normalize_scenario_name(entry["scenario"]) for entry in entries]
    else:
        names = [
            normalize_scenario_name(entry["scenario"])
            for entry in tasks_data
            if normalize_scenario_name(entry["scenario"]) in scenario_set
        ]
    if config.limit_scenarios:
        names = names[:config.limit_scenarios]
    return names


def _load_allowed_tasks(config: Config) -> set[tuple[str, int]] | None:
    if not config.allowed_tasks_path:
        return None
    return {
        (normalize_scenario_name(entry["scenario"]), entry["task_id"])
        for entry in tools_jsonl_load(config.allowed_tasks_path)
    }
