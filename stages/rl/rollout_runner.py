# -*- coding: utf-8 -*-
"""创建 rollout 环境、采样轨迹并调用 reward/judge。"""

import os
import shutil

import torch
from loguru import logger
from lib.interrupt import check_cancelled

from stages.rl.env import InProcessEnv
from stages.rl.reward import compute_reward
from stages.rl.rollout import rollout_batch


def collect_rollouts(state, item):
    """为一个 task 创建独立数据库，批量 rollout，并返回轨迹与 judge 结果。"""
    config = state.config

    #每条轨迹单独一个隔离的数据库环境
    envs = []
    try:
        for group_index in range(config.group_size):
            check_cancelled()
            run_dir = os.path.join(config.output_dir, f"{item.scenario}_task_{item.task_id}_g{group_index}")
            env = _start_env(state, item, run_dir, group_index)
            if env is not None:
                envs.append(env)
        with torch.no_grad():
            trajectories = rollout_batch(
                state.model, state.tokenizer, item.task, envs,
                config.max_steps, config.temperature, config.max_new_tokens,
                config.device, verbose=config.verbose,
                max_tokens_per_batch=config.max_tokens_per_batch,
                enable_thinking=config.enable_thinking,
            )
        results = []
        for trajectory, env in zip(trajectories, envs):
            check_cancelled()
            reward_type, judge_detail = compute_reward(
                env.initial_db_path, env.db_file_path,
                initial_snapshot=item.verifier.get("initial_snapshot"),
                tool_call_names=trajectory.get("tool_call_names"), task=item.task,
                messages=trajectory.get("messages"), judge_client=state.judge_client,
                judge_model=config.judge_model,
            )
            results.append((trajectory, reward_type, judge_detail))
        return results
    finally:
        for env in envs:
            env.close()
            shutil.rmtree(env.output_dir, ignore_errors=True)


def _start_env(state, item, run_dir: str, group_index: int):
    config = state.config
    for attempt in range(config.env_start_retries):#连续多次尝试启动环境，避免偶然失败
        check_cancelled()
        env = None
        started = False
        try:
            env = InProcessEnv(
                item.scenario, state.env_dir_map.get((item.scenario, item.task_id), ""),
                config.db_path, config.sample_path, run_dir, task_id=item.task_id,
                initial_db_source=item.verifier.get("db_path"),
            )
            env.start()
            started = True
            return env
        except Exception as exc:
            if attempt + 1 < config.env_start_retries:
                logger.warning(
                    f"{item.scenario} task_{item.task_id} g{group_index}: "
                    f"env 第 {attempt + 1} 次启动失败，重试：{type(exc).__name__}: {exc}"
                )
        finally:
            if env is not None and not started:
                env.close()
                shutil.rmtree(run_dir, ignore_errors=True)
    logger.error(f"{item.scenario} task_{item.task_id} g{group_index}: env 启动失败，跳过 rollout")
    return None

