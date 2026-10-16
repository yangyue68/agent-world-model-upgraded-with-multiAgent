"""将 db 阶段记录中的 task 描述与 task 阶段产物按任务键对齐。

默认只生成一个新的 ``gen_db_ma.aligned.jsonl``，不会修改原文件。
确认检查结果后，可使用 ``--in-place`` 原子替换原文件；替换前会自动生成
``<原文件>.backup_<时间戳>`` 备份。

任务键严格使用 ``(scenario, task_id)``：task 阶段的场景级 tasks 数组按顺序
展开为 task_1、task_2 ...。除 db 记录的 task 字段外，其他字段保持不变。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_TASK = _ROOT / "output" / "task" / "gen_tasks_ma.jsonl"
_DEFAULT_DB = _ROOT / "output" / "db" / "gen_db_ma.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: JSON 解析失败：{exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: 每行必须是 JSON 对象")
            rows.append(value)
    return rows


def _key(row: dict, source: str, line_number: int | None = None) -> tuple[str, str]:
    scenario = row.get("scenario")
    task_id = row.get("task_id")
    location = f"{source}:{line_number}" if line_number else source
    if not isinstance(scenario, str) or not scenario.strip():
        raise ValueError(f"{location}: 缺少非空 scenario")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError(f"{location}: 缺少非空 task_id")
    return scenario, task_id


def _build_task_map(rows: list[dict], source: str) -> dict[tuple[str, str], str]:
    """展开 task 阶段场景级 tasks，返回精确任务键到文本的映射。"""
    result: dict[tuple[str, str], str] = {}
    for line_number, row in enumerate(rows, 1):
        scenario = row.get("scenario")
        tasks = row.get("tasks")
        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError(f"{source}:{line_number}: 缺少非空 scenario")
        if not isinstance(tasks, list):
            raise ValueError(f"{source}:{line_number}: tasks 必须是数组")
        for index, task in enumerate(tasks, 1):
            key = (scenario, f"task_{index}")
            if key in result:
                raise ValueError(f"{source}:{line_number}: 重复任务键 {key}")
            if not isinstance(task, str) or not task.strip():
                raise ValueError(f"{source}:{line_number}: {key} 的任务描述必须是非空字符串")
            result[key] = task
    return result


def _build_db_index(rows: list[dict], source: str) -> dict[tuple[str, str], dict]:
    result = {}
    for line_number, row in enumerate(rows, 1):
        key = _key(row, source, line_number)
        if key in result:
            raise ValueError(f"{source}:{line_number}: 重复任务键 {key}")
        result[key] = row
    return result


def _preview(text: object, limit: int = 100) -> str:
    value = "" if text is None else str(text).replace("\r", " ").replace("\n", " ")
    return value if len(value) <= limit else value[:limit] + "..."


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def _replace_with_backup(source: Path, rows: list[dict]) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = source.with_name(source.name + f".backup_{timestamp}")
    shutil.copy2(source, backup)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=source.parent,
            prefix=source.stem + ".aligned_", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, source)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return backup


def align(task_path: Path, db_path: Path, output_path: Path | None = None,
          *, in_place: bool = False, strict: bool = False, write: bool = True) -> dict:
    if in_place and output_path is not None:
        raise ValueError("--in-place 不能与显式 --output 同时使用")
    if not task_path.is_file():
        raise FileNotFoundError(f"task 文件不存在：{task_path}")
    if not db_path.is_file():
        raise FileNotFoundError(f"db 文件不存在：{db_path}")
    if task_path.resolve() == db_path.resolve():
        raise ValueError("input task 和 input db 不能是同一个文件")

    task_map = _build_task_map(_read_jsonl(task_path), str(task_path))
    db_rows = _read_jsonl(db_path)
    db_map = _build_db_index(db_rows, str(db_path))
    missing_in_db = sorted(set(task_map) - set(db_map))
    orphan_db = sorted(set(db_map) - set(task_map))
    changes = []
    aligned_rows = []
    for row in db_rows:
        key = _key(row, str(db_path))
        old = row.get("task")
        new = task_map.get(key)
        if new is None:
            aligned_rows.append(row)
            continue
        if old != new:
            updated = dict(row)
            updated["task"] = new
            aligned_rows.append(updated)
            changes.append({"scenario": key[0], "task_id": key[1],
                            "old": old, "new": new})
        else:
            aligned_rows.append(row)

    if strict and (missing_in_db or orphan_db):
        raise ValueError(
            f"严格模式发现任务键不一致：task 中缺少 {len(missing_in_db)} 条 db 记录，"
            f"db 中多出 {len(orphan_db)} 条记录"
        )

    backup = None
    destination = db_path if in_place else (output_path or db_path.with_name(db_path.stem + ".aligned.jsonl"))
    if destination.resolve() == task_path.resolve():
        raise ValueError("output 不能覆盖 input task 文件")
    if not write:
        pass
    elif in_place:
        backup = _replace_with_backup(db_path, aligned_rows)
    else:
        _write_jsonl(destination, aligned_rows)
    return {
        "task_records": len(task_map),
        "db_records": len(db_rows),
        "changed": len(changes),
        "already_equal": len(db_rows) - len(changes) - len(orphan_db),
        "missing_in_db": missing_in_db,
        "orphan_db": orphan_db,
        "destination": str(destination),
        "backup": str(backup) if backup else None,
        "changes": changes,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按 scenario/task_id 对齐 db 阶段的 task 描述")
    parser.add_argument("--input-task", type=Path, default=_DEFAULT_TASK,
                        help=f"task 阶段输入（默认：{_DEFAULT_TASK}）")
    parser.add_argument("--input-db", type=Path, default=_DEFAULT_DB,
                        help=f"db 阶段输入（默认：{_DEFAULT_DB}）")
    parser.add_argument("--output", type=Path, default=None,
                        help="输出 JSONL；默认生成 gen_db_ma.aligned.jsonl")
    parser.add_argument("--in-place", action="store_true",
                        help="原子替换 input-db，并在旁边生成 backup 文件")
    parser.add_argument("--strict", action="store_true",
                        help="发现 task/db 任务键缺失或多出时退出失败，不写输出")
    parser.add_argument("--dry-run", action="store_true",
                        help="只检查并打印统计，不写文件")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.dry_run:
        # dry-run 仍执行完整关联，但不落盘。
        result = align(args.input_task, args.input_db, args.output,
                       in_place=False, strict=args.strict, write=False)
    else:
        result = align(args.input_task, args.input_db, args.output,
                       in_place=args.in_place, strict=args.strict)
    print(f"task 任务数：{result['task_records']}")
    print(f"db 记录数：{result['db_records']}")
    print(f"已修改 task 描述：{result['changed']}")
    print(f"原本已一致：{result['already_equal']}")
    print(f"task 中 db 缺失：{len(result['missing_in_db'])}")
    if result["missing_in_db"]:
        print("  " + "\n  ".join(f"{s}/{t}" for s, t in result["missing_in_db"]))
    print(f"db 中 task 没有对应项：{len(result['orphan_db'])}")
    if result["orphan_db"]:
        print("  " + "\n  ".join(f"{s}/{t}" for s, t in result["orphan_db"]))
    if result["backup"]:
        print(f"原 db 备份：{result['backup']}")
    if args.dry_run:
        print("dry-run：未写入文件")
    else:
        print(f"输出文件：{result['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
