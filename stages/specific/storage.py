"""任务精确关联、断点续跑与逐行持久化，不访问或修改数据库。"""

import json
from pathlib import Path

from loguru import logger
from .validators import validate_spec


def read_jsonl(path):
    rows = []
    with Path(path).open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("记录不是对象")
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: {exc}") from exc
            rows.append(row)
    return rows


def task_key(row):
    scenario, task_id = row.get("scenario"), row.get("task_id")
    if not isinstance(scenario, str) or not scenario or not isinstance(task_id, str) or not task_id:
        raise ValueError("任务记录缺少 scenario/task_id")
    return scenario, task_id


def indexed(rows, source):
    result = {}
    for row in rows:
        key = task_key(row)
        if key in result:
            raise ValueError(f"{source} 有重复任务键 {key}，请先消除歧义")
        result[key] = row
    return result


def load_items(config):
    # 精确任务键关联，不用有碰撞风险的目录名或猜测 task 索引。
    tasks = []
    for row in read_jsonl(config.input_task):
        if not isinstance(row.get("tasks"), list):
            raise ValueError("input_task 需要场景级 tasks 数组")
        tasks.extend({"scenario": row.get("scenario"), "task_id": f"task_{i}", "task": task}
                     for i, task in enumerate(row["tasks"], 1))
    task_map = indexed(tasks, "task")
    db_map = indexed(read_jsonl(config.input_db), "db")
    sample_map = indexed(read_jsonl(config.input_sample), "sample")
    items, failures = [], []
    for key, sample in sample_map.items():
        if config.allowed_scenarios and key[0] not in config.allowed_scenarios:
            continue
        task, db = task_map.get(key), db_map.get(key)
        reason = ""
        if task is None or db is None:
            reason = "sample 保留的任务缺少对应 task 或 db 记录"
        elif not isinstance(task.get("task"), str) or not task["task"].strip():
            reason = "任务文本必须为非空字符串"
        elif not isinstance(db.get("db_schema"), dict) or not db["db_schema"].get("tables"):
            reason = "缺少非空 db_schema.tables"
        elif any(row.get("task") is not None and row["task"] != task["task"] for row in (db, sample)):
            reason = "同一任务键的 task/db/sample 任务文本不一致，禁止串用产物"
        if reason:
            failure = {"scenario": key[0], "task_id": key[1], "task": sample.get("task"),
                       "stage": "input", "reason": reason}
            failures.append(failure)
        else:
            items.append({**task, "db_item": db, "sample_item": sample})
    items.sort(key=lambda item: (item["scenario"], int(item["task_id"].split("_")[1])))
    logger.info(f"sample 保留 {len(sample_map)} 个任务，可关联 {len(items)} 个，输入问题 {len(failures)} 个")
    return items, failures


def append_jsonl(path, record):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def resume_keys(path):
    """返回可续跑的 (scenario,task_id) 集合；损坏记录仅警告并跳过，不阻断。"""
    if not Path(path).exists():
        return set()
    try:
        records = indexed(read_jsonl(path), "成功输出")
    except ValueError as exc:
        logger.warning(f"[续跑] {path} 索引失败：{exc}，视为无已完成任务")
        return set()
    valid = {}
    for key, row in records.items():
        if row.get("spec_version") != "specific-v1":
            logger.warning(f"[续跑] 跳过非 specific-v1 产物 {key}")
            continue
        api_spec = row.get("api_spec")
        problems = validate_spec(api_spec.get("api_groups")) if isinstance(api_spec, dict) else ["结构损坏"]
        if problems:
            logger.warning(f"[续跑] 跳过损坏产物 {key}: {problems[:1]}")
            continue
        valid[key] = row
    return set(valid)
