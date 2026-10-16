# -*- coding: utf-8 -*-
"""Reward calculation from an exact database diff and the rollout trajectory."""

from lib.db_diff import diff_db, snapshot_db
from lib.llm_judge import llm_judge


def check_tool_order(tool_call_names: list) -> bool:
    """Check the protocol order used by the rollout agent."""
    if not tool_call_names or tool_call_names[0][0] != "list_tools":
        return False
    queried = set()
    for outer, inner in tool_call_names:
        if outer == "get_tool_detail_of":
            queried.add(inner)
        elif outer == "call_tool" and inner not in queried:
            return False
    return True


def extract_final_answer(traj: dict) -> str:
    """Return the last assistant message; final_answer stays in the trajectory."""
    for msg in reversed(traj.get("messages", [])):
        if msg.get("role") == "assistant":
            return msg.get("content", "")
    return ""


def _has_database_changes(diff: dict | None) -> bool:
    if not diff:
        return False
    summary = diff.get("summary", {})
    schema = diff.get("schema_changes", {})
    return bool(
        any(summary.get(k, 0) for k in ("inserted", "updated", "pk_updated", "deleted", "tables_changed"))
        or any(schema.get(k) for k in ("created_tables", "dropped_tables", "added_columns", "dropped_columns"))
    )


def compute_reward(initial_db_path: str, final_db_path: str,
                   initial_snapshot: dict | None = None,
                   tool_call_names: list | None = None,
                   task: str | None = None,
                   messages: list | None = None,
                   judge_client=None,
                   judge_model: str | None = None) -> tuple:
    """Judge one rollout using the task-level initial snapshot and exact final diff.

    ``initial_snapshot`` is reused across rollouts when it is present in sample output.
    An empty diff is represented as ``None`` in the judge input.
    """
    try:
        initial = initial_snapshot if initial_snapshot is not None else snapshot_db(initial_db_path)
        diff = diff_db(initial_db_path, final_db_path, initial_snap=initial)
    except Exception as e:
        return "judge_error", f"database snapshot/diff failed: {type(e).__name__}: {e}"

    database_state = {
        "initial_snapshot": initial,
        "diff": diff if _has_database_changes(diff) else None,
    }
    if judge_client is None or not judge_model:
        return "judge_error", "LLM judge endpoint/model not configured"

    classification, judge_result = llm_judge(
        judge_client, judge_model, task, messages or [], database_state
    )
    if classification == "judge_error":
        detail = judge_result.get("error", "LLM judge failed") if isinstance(judge_result, dict) else str(judge_result)
        return "judge_error", detail

    order_ok = (tool_call_names is None) or check_tool_order(tool_call_names)
    if classification == "complete":
        return ("complete" if order_ok else "ordered"), None
    if classification == "server_error":
        return "env_error", "LLM judge classified the rollout as a server/environment error"
    if classification == "agent_error":
        return "others", None
    return ("ordered" if order_ok else "others"), None
