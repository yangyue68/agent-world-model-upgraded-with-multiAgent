# -*- coding: utf-8 -*-
"""批量给 sample 产物补齐 initial_snapshot / initial_snapshot_len，并按体积门槛剔除。

背景：
  sample 阶段新增了「初始快照落盘 + 体积门槛」逻辑，但现有 3007 条产物是在此之前生成的，
  里面没有 initial_snapshot 字段。本脚本用已经灌好数据的 .db 文件反查初始状态，
  把快照和长度补进产物，让 RL 侧无需重跑 sample 阶段即可使用。

关键点：
  - 严格按 (scenario, task_id) 配对 sample 产物与 db 文件，不依赖数组下标或文件名猜测。
  - 默认写新文件（<output>.with_snapshot.jsonl），不原地覆盖，确认后再自行替换。
  - 超过门槛的行不进新产物，同时追加到 bad_output，理由里带上实际长度和门槛。

用法：
  python -m ma_upgrade.scripts.backfill_sample_snapshots
  python -m ma_upgrade.scripts.backfill_sample_snapshots --max-snapshot-chars 10240 --in-place
  python -m ma_upgrade.scripts.backfill_sample_snapshots --limit 20 --preview 3
"""

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path


_MA_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _MA_ROOT.parent
for _p in (str(_MA_ROOT), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.db_diff import snapshot_db, snapshot_size  # noqa: E402
from lib.tools import resolve_project_path, safe_dir_name  # noqa: E402


DEFAULT_SAMPLE = _MA_ROOT / "output" / "sample" / "gen_sample_ma.jsonl"
DEFAULT_DB = _MA_ROOT / "output" / "db" / "gen_db_ma.jsonl"
DEFAULT_BAD = _MA_ROOT / "output" / "sample" / "sample_bad_tasks.jsonl"
DEFAULT_DB_DIR = _MA_ROOT / "output" / "db" / "databases"


def resolve_db_path(rec, db_dir, project_root):
    """取一条 sample 记录对应的 .db 绝对路径。

    优先用产物里的 db_path（相对路径按项目根解析）；取不到时按
    output/db/databases/<safe_dir_name(scenario)>_<task_id>.db 兜底。
    """
    raw = rec.get("db_path") or ""
    candidates = []
    if raw:
        # 不能用 os.path.isabs：Windows 盘符路径（E:\...）在 Linux 上会被判成
        # 「相对路径」，os.path.join 之后得到 <root>/E:/... 这种永远不存在的
        # 垃圾路径。统一交给 resolve_project_path 按项目锚点重定位。
        candidates.append(str(resolve_project_path(raw, project_root)))
        candidates.append(str(resolve_project_path(raw, _MA_ROOT)))
    candidates.append(
        os.path.join(db_dir, "%s_%s.db" % (safe_dir_name(rec.get("scenario", "")), rec.get("task_id", "")))
    )
    for p in candidates:
        if p and os.path.exists(p):
            return os.path.normpath(p)
    return None


def main():
    p = argparse.ArgumentParser(description="给 sample 产物补齐初始快照并按体积门槛剔除")
    p.add_argument("--sample", default=str(DEFAULT_SAMPLE))
    p.add_argument("--db", default=str(DEFAULT_DB), help="db 产物（用于校验 key 是否一致，可选）")
    p.add_argument("--db-dir", default=str(DEFAULT_DB_DIR))
    p.add_argument("--bad-output", default=str(DEFAULT_BAD))
    p.add_argument("--output", default="", help="输出路径；默认 <sample>.with_snapshot.jsonl")
    p.add_argument("--max-snapshot-chars", type=int, default=10 * 1024,
                   help="初始快照字符数上限，超过则剔除（默认 10240 = 10 KB）")
    p.add_argument("--in-place", action="store_true", help="直接覆盖原 sample 产物（先写临时文件再替换）")
    p.add_argument("--limit", type=int, default=None, help="只处理前 N 条（调试用）")
    p.add_argument("--preview", type=int, default=3, help="打印前 N 条快照摘要，0 = 不打印")
    args = p.parse_args()

    if not os.path.exists(args.sample):
        raise SystemExit("[error] sample 产物不存在：%s" % args.sample)

    output = args.output or (
        args.sample if args.in_place
        else args.sample.replace(".jsonl", "") + ".with_snapshot.jsonl"
    )
    if os.path.normpath(output) == os.path.normpath(args.sample) and not args.in_place:
        raise SystemExit("[error] 输出与输入相同，请显式加 --in-place")

    records = []
    with open(args.sample, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    if args.limit is not None:
        records = records[: args.limit]

    # db 产物的 key 集合，仅用于报告配对一致性
    db_keys = set()
    if os.path.exists(args.db):
        with open(args.db, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    db_keys.add((d.get("scenario"), d.get("task_id")))

    print("[load] sample %d 条，门槛 %d 字符" % (len(records), args.max_snapshot_chars))
    print("[write] 输出 -> %s" % output)

    kept = []
    dropped = []
    status = Counter()
    missing_db = []
    dup_keys = Counter((r.get("scenario"), r.get("task_id")) for r in records)
    dup_keys = {k: v for k, v in dup_keys.items() if v > 1}
    if dup_keys:
        print("[warn] sample 产物里有 %d 个重复 (scenario, task_id)，会各自处理" % len(dup_keys))

    for i, rec in enumerate(records, 1):
        key = (rec.get("scenario"), rec.get("task_id"))
        db_path = resolve_db_path(rec, args.db_dir, _PROJECT_ROOT)
        if db_path is None:
            missing_db.append(key)
            dropped.append({
                "scenario": rec.get("scenario"), "task_id": rec.get("task_id"),
                "task": rec.get("task"),
                "reason": "找不到对应的 .db 文件，无法读取初始快照",
            })
            status["missing_db"] += 1
            continue

        try:
            snap = snapshot_db(db_path)
        except Exception as e:
            dropped.append({
                "scenario": rec.get("scenario"), "task_id": rec.get("task_id"),
                "task": rec.get("task"),
                "reason": "读取初始快照失败：%s: %s" % (type(e).__name__, e),
            })
            status["snapshot_error"] += 1
            continue

        snap_len = snapshot_size(snap)
        if snap_len > args.max_snapshot_chars:
            dropped.append({
                "scenario": rec.get("scenario"), "task_id": rec.get("task_id"),
                "task": rec.get("task"),
                "reason": "初始快照过大：%d 字符（门槛 %d）" % (snap_len, args.max_snapshot_chars),
                "initial_snapshot_len": snap_len,
                "max_snapshot_chars": args.max_snapshot_chars,
            })
            status["too_large"] += 1
            continue

        out = dict(rec)
        out["initial_snapshot"] = snap
        out["initial_snapshot_len"] = snap_len
        kept.append(out)
        status["kept"] += 1

        if i % 200 == 0 or i == len(records):
            print("[progress] %d/%d  已保留 %d，剔除 %d" % (i, len(records), len(kept), len(dropped)))

        if args.preview > 0 and len(kept) == args.preview:
            print("\n--- 快照样例（截断显示）---")
            for r in kept[: args.preview]:
                s = json.dumps(r["initial_snapshot"], ensure_ascii=False, default=str)
                print("\n%s / %s  快照 %d 字符，%d 张表" % (
                    r.get("scenario"), r.get("task_id"), r["initial_snapshot_len"], len(r["initial_snapshot"])))
                print(s[:1200] + (" ...[截断]" if len(s) > 1200 else ""))
            args.preview = 0

    # Keep the original file intact until the complete replacement has been
    # serialized.  This matters for --in-place runs and for retries after a
    # single non-JSON SQLite value is encountered.
    output_abs = os.path.abspath(output)
    tmp_output = output_abs + ".tmp"
    try:
        os.makedirs(os.path.dirname(output_abs), exist_ok=True)
        with open(tmp_output, "w", encoding="utf-8") as f:
            for rec in kept:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        os.replace(tmp_output, output_abs)
    except Exception:
        try:
            os.remove(tmp_output)
        except OSError:
            pass
        raise

    print("\n" + "=" * 78)
    print("汇总")
    print("  读入            : %d" % len(records))
    for k in ("kept", "too_large", "missing_db", "snapshot_error"):
        if status.get(k):
            print("  %-15s : %d" % (k, status[k]))
    print("  剔除合计        : %d" % len(dropped))
    if kept:
        lens = sorted(r["initial_snapshot_len"] for r in kept)
        print("  保留快照字符数  : min=%d  median=%d  mean=%d  p90=%d  max=%d" % (
            lens[0], lens[len(lens) // 2], round(sum(lens) / len(lens)),
            lens[min(len(lens) - 1, int(0.9 * len(lens)))], lens[-1]))
    print("  输出            : %s" % output)

    if dropped:
        os.makedirs(os.path.dirname(os.path.abspath(args.bad_output)), exist_ok=True)
        existing = {}
        if os.path.exists(args.bad_output):
            with open(args.bad_output, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        b = json.loads(line)
                        existing[(b.get("scenario"), b.get("task_id"))] = b
        added = 0
        for b in dropped:
            k = (b.get("scenario"), b.get("task_id"))
            if k in existing:
                continue
            existing[k] = b
            added += 1
        with open(args.bad_output, "w", encoding="utf-8") as f:
            for b in existing.values():
                f.write(json.dumps(b, ensure_ascii=False, default=str) + "\n")
        print("  bad_output      : %s（新增 %d 条，累计 %d 条）" % (
            args.bad_output, added, len(existing)))

    if db_keys:
        kept_keys = {(r.get("scenario"), r.get("task_id")) for r in kept}
        in_db = len(kept_keys & db_keys)
        print("\n[check] 保留的 key 在 db 产物中存在: %d / %d" % (in_db, len(kept_keys)))
        not_in_db = kept_keys - db_keys
        if not_in_db:
            print("[warn] 有 %d 个 key 不在 db 产物里，例如：%s" % (
                len(not_in_db), list(not_in_db)[:3]))

    if missing_db:
        print("\n[warn] 有 %d 条找不到 .db，例如：%s" % (len(missing_db), missing_db[:3]))


if __name__ == "__main__":
    main()
