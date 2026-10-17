# -*- coding: utf-8 -*-
"""处理 rollout/judge 结果并转换为 GRPO reward。"""

from loguru import logger

from stages.rl.checkpoint import append_bad_task


def process_results(state, item, results, epoch: int):
    """过滤环境/judge 错误，记录坏任务，并返回有效轨迹和 reward。"""
    trajectories = []
    rewards = []
    config = state.config
    for trajectory, reward_type, judge_detail in results:
        key = (item.scenario, item.task_id)
        if trajectory.get("has_5xx"):
            _record_env_error(state, item, trajectory, epoch, "env_5xx")
            if key in state.bad_tasks:
                break
            continue
        if reward_type == "env_error":
            _record_env_error(state, item, trajectory, epoch, "env_error", judge_detail)
            if key in state.bad_tasks:
                break
            continue
        if reward_type == "judge_error":
            count = state.judge_error_counter.get(key, 0) + 1
            state.judge_error_counter[key] = count
            state.stats["bad_rollouts"] += 1
            logger.warning(f"judge_error：{item.scenario} task_{item.task_id} 累计 {count} 次")
            if count == config.bad_verify_threshold:
                state.bad_tasks.add(key)
                append_bad_task(
                    state.bad_tasks_path, item.scenario, item.task_id,
                    judge_detail or "judge_error", count, type_="judge_error",
                    epoch=epoch + 1,
                )
                state.stats["bad_tasks"] += 1
                break
            continue

        total_calls = trajectory.get("total_calls", 0)
        successful_calls = trajectory.get("successful_calls", 0)
        process_rate = successful_calls / total_calls if total_calls else 0.0
        reward = 1.0 + 0.3 * process_rate if reward_type == "complete" else (
            0.3 + 0.3 * process_rate if reward_type == "ordered" else (
                0.1 + 0.25 * process_rate if reward_type == "partial" else 0.0
            )
        )
        trajectories.append(trajectory)
        rewards.append(reward)
    return trajectories, rewards


def _record_env_error(state, item, trajectory, epoch: int, error_type: str, reason: str | None = None):
    key = (item.scenario, item.task_id)
    count = state.env_5xx_counter.get(key, 0) + 1
    state.env_5xx_counter[key] = count
    state.stats["bad_rollouts"] += 1
    logger.warning(f"{error_type}：{item.scenario} task_{item.task_id} 累计 {count} 次")
    if count != state.config.bad_env_threshold:
        return
    state.bad_tasks.add(key)
    details = trajectory.get("error_details", []) or []
    if details:
        tool = "|".join(dict.fromkeys(tool_name for tool_name, _ in details))
        error_reason = details[-1][1]
    else:
        tool = None
        error_reason = reason or error_type
    append_bad_task(
        state.bad_tasks_path, item.scenario, item.task_id, error_reason, count,
        type_=error_type, epoch=epoch + 1, tool=tool,
    )
    state.stats["bad_tasks"] += 1

