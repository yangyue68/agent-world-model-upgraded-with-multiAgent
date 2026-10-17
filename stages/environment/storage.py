"""Input joins, JSONL persistence and stable task fingerprints."""
from pathlib import Path
import hashlib
import json

from loguru import logger
from lib.single_file_env import ENV_VERSION


def read_jsonl(path):
    records = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except Exception as exc:
                records.append({"stage": "input", "line": line_no, "reason": str(exc)})
    return records


def append_jsonl(path, record):
    """逐行追加持久化，与 specific 阶段一致：续跑不清空、每条结果即时落盘。"""
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def resume_keys(path, root):
    """可续跑跳过的 (scenario, task_id) 集合。损坏行、非当前版本、产物目录缺失的记录只警告并视为未完成。"""
    if not Path(path).exists():
        return set()
    valid = set()
    for row in read_jsonl(path):
        if not row.get("env_dir") or not row.get("traj_dir"):
            continue
        key = task_key(row)
        if row.get("env_version") != ENV_VERSION:
            logger.warning(f"[续跑] {key} 产物版本 {row.get('env_version')} 非 {ENV_VERSION}，重跑")
            continue
        if not (Path(root) / row["env_dir"] / "main.py").is_file() or not (Path(root) / row["traj_dir"] / "traj.json").is_file():
            logger.warning(f"[续跑] {key} 的 envs/trajectory 目录缺失，重跑")
            continue
        valid.add(key)
    return valid


def task_key(record):
    return str(record.get("scenario", "")), str(record.get("task_id", ""))


def fingerprint(record):
    payload = {key: record.get(key) for key in ("scenario", "task_id", "task", "api_spec", "db_schema", "db_path")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def portable(path, root):
    value = Path(path).resolve()
    try:
        return value.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(value)


def merge_inputs(config):
    specs = {task_key(row): row for row in read_jsonl(config.input_spec) if "reason" not in row}
    tasks = {}
    for row in read_jsonl(config.input_task):
        if isinstance(row.get("tasks"), list):
            for index, task in enumerate(row["tasks"], 1):
                tasks[(row.get("scenario"), f"task_{index}")] = {"scenario": row.get("scenario"), "task_id": f"task_{index}", "task": task}
        elif row.get("task_id"):
            tasks[task_key(row)] = row
    dbs = {task_key(row): row for row in read_jsonl(config.input_db) if "reason" not in row}
    samples = {task_key(row): row for row in read_jsonl(config.input_sample) if "reason" not in row}
    items, failures = [], []
    for key, spec in specs.items():
        task = tasks.get(key)
        db = dbs.get(key)
        sample = samples.get(key)
        if not task or not db or not sample or not db.get("db_path"):
            failures.append({"scenario": key[0], "task_id": key[1], "stage": "input", "reason": "specific/task/db/sample 产物无法按场景和任务 ID 对齐"})
            continue
        item = {"scenario": key[0], "task_id": key[1], "task": task.get("task", spec.get("task", "")),
                "api_spec": spec.get("api_spec", {}), "db_schema": db.get("db_schema", {}),
                "db_path": db["db_path"], "initial_snapshot": sample.get("initial_snapshot", {}),
                "initial_snapshot_len": sample.get("initial_snapshot_len")}
        if not Path(item["db_path"]).is_absolute():
            project_root = Path(config.input_db).resolve().parents[2]
            item["db_path"] = str((project_root / item["db_path"]).resolve())
        if not Path(item["db_path"]).is_file():
            failures.append({"scenario": key[0], "task_id": key[1], "stage": "input", "reason": f"数据库不存在: {item['db_path']}"})
            continue
        item["input_fingerprint"] = fingerprint(item)
        items.append(item)
    return items, failures
