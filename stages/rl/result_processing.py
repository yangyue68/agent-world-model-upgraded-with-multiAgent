# -*- coding: utf-8 -*-
"""处理 rollout/judge 结果并转换为 GRPO reward。"""

from loguru import logger

from stages.rl.checkpoint import append_bad_task
from stages.rl.reward import check_tool_order


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _judge_completion_score(judge_detail, reward_type: str) -> float:
    """Extract a continuous completion signal while accepting old judge output."""
    if isinstance(judge_detail, dict):
        raw = judge_detail.get("completion_score")
        if isinstance(raw, (int, float)):
            return _clamp(raw / 100.0)

        # Backward compatibility: confidence_score is ordered as
        # complete/incomplete/server_error/agent_error.
        confidence = judge_detail.get("confidence_score")
        if isinstance(confidence, list) and confidence:
            index = {"complete": 0, "ordered": 0, "partial": 1,
                     "agent_error": 3, "others": 1}.get(reward_type, 1)
            if index < len(confidence) and isinstance(confidence[index], (int, float)):
                return _clamp(confidence[index] / 100.0)

    return {"complete": 1.0, "ordered": 0.7, "partial": 0.25}.get(reward_type, 0.0)


def _trajectory_signal(trajectory: dict) -> dict[str, float]:
    """Build deterministic process signals independent of the judge wording."""
    total_calls = int(trajectory.get("total_calls", 0) or 0)
    successful_calls = int(trajectory.get("successful_calls", 0) or 0)
    successful_calls = max(0, min(successful_calls, total_calls))
    process_rate = successful_calls / total_calls if total_calls else 0.0
    # A capped call-count term rewards making useful progress without
    # encouraging an unbounded number of redundant reads.
    call_coverage = _clamp(successful_calls / 4.0)
    messages = trajectory.get("messages", []) or []
    final_answer = 1.0 if any(
        msg.get("role") == "assistant" and "<final_answer>" in str(msg.get("content", ""))
        for msg in messages if isinstance(msg, dict)
    ) else 0.0
    violations = int(trajectory.get("format_violations", 0) or 0)
    protocol_quality = _clamp(1.0 - 0.15 * violations)
    tool_calls = trajectory.get("tool_call_names", []) or []
    protocol_order = 1.0 if check_tool_order(tool_calls) else 0.0
    return {
        "process_rate": process_rate,
        "call_coverage": call_coverage,
        "final_answer": final_answer,
        "protocol_quality": protocol_quality,
        "protocol_order": protocol_order,
    }


def _continuous_reward(trajectory: dict, reward_type: str, judge_detail) -> float:
    """Combine judge evidence and trajectory evidence into a bounded reward.

    The score remains outcome-led, while process terms make partial trajectories
    distinguishable enough for the global-baseline fallback to be useful.
    """
    signal = _trajectory_signal(trajectory)
    completion = _judge_completion_score(judge_detail, reward_type)
    evidence = (
        0.55 * completion
        + 0.20 * signal["process_rate"]
        + 0.10 * signal["call_coverage"]
        + 0.10 * signal["final_answer"]
        + 0.025 * signal["protocol_quality"]
        + 0.025 * signal["protocol_order"]
    )
    evidence = _clamp(evidence)
    if reward_type == "complete":
        return 0.90 + 0.40 * evidence       # 0.90 ~ 1.30
    if reward_type == "ordered":
        return 0.45 + 0.35 * evidence       # 完成但协议顺序有问题
    if reward_type == "partial":
        return 0.05 + 0.35 * evidence       # 有效的部分进展
    if reward_type == "agent_error":
        return 0.02 * evidence              # 保留很弱的探索信号
    return 0.01 * evidence


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

        reward = _continuous_reward(trajectory, reward_type, judge_detail)
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

