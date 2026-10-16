# -*- coding: utf-8 -*-
"""按 task 执行 rollout、reward、advantage 和 policy 更新。"""

import time

import torch
from loguru import logger
from lib.interrupt import check_cancelled

from stages.rl.checkpoint import save_checkpoint
from stages.rl.objectives import compute_group_advantages, compute_log_prob
from stages.rl.preparation import TrainingState, prepare_training
from stages.rl.result_processing import process_results
from stages.rl.rollout_runner import collect_rollouts


def train(config):
    check_cancelled()
    state = prepare_training(config)
    for epoch in range(config.num_epochs):
        logger.info(f"===== Epoch {epoch + 1}/{config.num_epochs}（共 {len(state.train_items)} 个 task）=====")
        for index, item in enumerate(state.train_items):
            check_cancelled()
            if (item.scenario, item.task_id) in state.bad_tasks:
                continue
            if config.checkpoint_every and index > 0 and index % config.checkpoint_every == 0:
                save_checkpoint(state.model, state.tokenizer, config.output_dir, index)
            logger.info(
                f"===== [Epoch {epoch + 1}/{config.num_epochs}] task {index + 1}/{len(state.train_items)}："
                f"{item.scenario} task_{item.task_id} 开始 rollout（group_size={config.group_size}）====="
            )
            state.model.gradient_checkpointing_disable()
            started = time.time()
            results = collect_rollouts(state, item)
            logger.info(f"[{item.scenario} task_{item.task_id}] rollout 完成，耗时 {time.time() - started:.1f}s，得到 {len(results)} 条轨迹")
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            trajectories, rewards = process_results(state, item, results, epoch)
            state.model.gradient_checkpointing_enable()
            if len(rewards) < 2:
                state.stats["skipped_few"] += 1
                logger.warning(f"{item.scenario} task_{item.task_id} 有效 rollout 仅 {len(rewards)} 条，跳过更新")
                continue
            advantages = _compute_advantages(state, rewards, item)
            if max(abs(value) for value in advantages) < 1e-8:
                state.stats["skipped_zero_adv"] += 1
                logger.info(f"[{item.scenario} task_{item.task_id}] advantage 全 0，跳过反向传播")
                continue
            steps = [
                (advantage, prompt_ids, completion_ids)
                for trajectory, advantage in zip(trajectories, advantages)
                for prompt_ids, completion_ids in zip(trajectory["prompt_ids"], trajectory["completion_ids"])
            ]
            if steps:
                _update_policy(state, steps, item, rewards)
        _log_epoch_stats(state, epoch)
    check_cancelled()
    save_checkpoint(state.model, state.tokenizer, config.output_dir, len(state.train_items) * config.num_epochs)
    logger.success("训练完成：最终权重已保存到 checkpoint/")


def _compute_advantages(state: TrainingState, rewards: list[float], item):
    batch_mean = sum(rewards) / len(rewards)
    state.baseline = (state.baseline * state.baseline_n + batch_mean) / (state.baseline_n + 1)
    state.baseline_n += 1
    reward_tensor = torch.tensor(rewards, dtype=torch.float32)
    if reward_tensor.std() < state.config.baseline_eps:
        logger.info(f"[{item.scenario} task_{item.task_id}] reward 无方差，回退全局 baseline={state.baseline:.4f}")
        return [float(value - state.baseline) for value in rewards]
    return compute_group_advantages(rewards)


def _update_policy(state: TrainingState, steps, item, rewards):
    started = time.time()
    state.stats["updated"] += 1
    state.optimizer.zero_grad()
    total_loss = 0.0
    for advantage, prompt_ids, completion_ids in steps:
        check_cancelled()
        log_prob = compute_log_prob(state.model, prompt_ids, completion_ids, state.config.device, state.config.logsumexp_chunk_size)
        check_cancelled()
        loss = (-advantage * log_prob) / len(steps)
        loss.backward()
        total_loss += loss.item()
    check_cancelled()
    state.optimizer.step()
    logger.info(f"[{item.scenario} task_{item.task_id}] GRPO 更新完成，loss={total_loss:.4f}，耗时 {time.time() - started:.1f}s，rewards={rewards}")


def _log_epoch_stats(state, epoch: int):
    stats = state.stats
    logger.info(
        f"===== Epoch {epoch + 1}/{state.config.num_epochs} 统计：反向传播 {stats['updated']} 次 | "
        f"advantage 全 0 跳过 {stats['skipped_zero_adv']} 次 | 有效 rollout 太少跳过 {stats['skipped_few']} 次 | "
        f"坏 rollout {stats['bad_rollouts']} 次 | 淘汰任务 {stats['bad_tasks']} 次 ====="
    )
