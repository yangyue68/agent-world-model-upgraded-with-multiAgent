"""从 schema + sample 产物重建任务级 .db，并统计每个库的数据快照长度。

背景：output/db/databases/ 里的 3027 个 .db 目前全是 0 字节（本该有表结构和样例数据）。
本脚本分两步：

  1) rebuild  —— 读取 output/db/gen_db_ma.jsonl（表结构）和 output/sample/gen_sample_ma.jsonl
                （样例 INSERT），按 (scenario, task_id) 配对，逐个重建 .db。
                复用项目已有的 stages.db.db_utils.create_sqlite_database 建表（含索引），
                再把 sample_data 里的 INSERT 语句执行进去。

  2) snapshot —— 对每个 .db 的所有表执行 SELECT *，拼成紧凑 JSON 快照，
                统计长度（字符数 + utf-8 字节数）的总数/最大/最小/平均/中位数/p90，
                并打印前 N 个库的快照。

快照格式（每张表）：
    "表名": {"columns": [列名...], "pk": [主键列...], "rows": [[值...], ...]}
行用数组而不是字典，列名只在表级出现一次，比每行重复列名省一半左右。

用法（默认一步跑完 rebuild + snapshot）：
    python -m ma_upgrade.rebuild_and_snapshot

常用参数：
    --limit 20          只处理前 20 个任务（快速试跑）
    --skip-rebuild      跳过重建，只对现有 .db 做快照统计
    --preview 10        打印前 10 个快照（默认 10，设 0 不打印）
    --preview-chars 0   每个快照最多打印多少字符，0 = 不截断（默认不截断）
    --workers 8         重建库的并发线程数（默认 8）
    --foreign-keys      插入时开启 PRAGMA foreign_keys=ON（默认 OFF，尽量多恢复数据）
    --report <path>     统计结果写入 JSON，默认 output/db/snapshot_stats.json

配对规则：db 和 sample 都按 (scenario, task_id) 精确匹配；若同一 key 有重复记录，取最后一条
（与 train_grpo.py 里 verifier_map 的覆盖行为一致）。sample 缺失时只建表不插数据，记为 schema_only。
"""

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path

from lib.interrupt import (
    InterruptibleThreadPoolExecutor as ThreadPoolExecutor,
    as_completed,
    check_cancelled,
    run_cli,
)


_MA_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _MA_ROOT.parent
for _p in (str(_MA_ROOT), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_DB_DIR = _MA_ROOT / "output" / "db" / "databases"
DEFAULT_SCHEMA = _MA_ROOT / "output" / "db" / "gen_db_ma.jsonl"
DEFAULT_SAMPLE = _MA_ROOT / "output" / "sample" / "gen_sample_ma.jsonl"
DEFAULT_REPORT = _MA_ROOT / "output" / "db" / "snapshot_stats.json"


def safe_dir_name(name) -> str:
    """把场景名转成安全的文件名词（与 lib.tools.safe_dir_name 行为一致）。

  空格和 Windows 非法字符 -> 连字符，去首尾连字符，空名兜底为 unnamed。保留大小写。
    """
    s = re.sub(r'[<>:"/\\|?*\s]+', '-', str(name or '').strip())
    s = s.strip('-').strip()
    return s or 'unnamed'


def create_sqlite_database(db_dir: str, db_filename: str, db_schema: dict) -> tuple[int, int, list]:
    """按 db_schema 建库（ddl + indexes + examples），返回 (成功表数, 失败表数, 错误样例)。

    与 stages.db.db_utils.create_sqlite_database 行为一致，但不 import 项目模块，
    这样在没装 loguru/orjson 的环境里也能跑。
    """
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, db_filename)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    ok = 0
    failed = 0
    errors = []
    for table in db_schema.get("tables", []):
        name = table.get("name", "unknown")
        try:
            cursor.execute(table["ddl"])
            for index_sql in table.get("indexes", []):
                cursor.execute(index_sql)
            for example_sql in table.get("examples", []):
                cursor.execute(example_sql)
            ok += 1
        except Exception as e:
            failed += 1
            if len(errors) < 3:
                errors.append(f"{name}: {e}")
    conn.commit()
    conn.close()
    return ok, failed, errors


def load_jsonl_by_key(path, key_fields=("scenario", "task_id")):
    """读 JSONL，按 (scenario, task_id) 建索引；重复 key 取最后一条。"""
    index = {}
    total = 0
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[warn] {path} 第 {line_no} 行 JSON 解析失败，跳过：{e}")
                continue
            total += 1
            index[tuple(rec.get(k) for k in key_fields)] = rec
    return index, total


def insert_sample_data(db_path: str, sample_data: dict, foreign_keys: bool = False) -> tuple[int, int, list]:
    """把 sample_data 里的 INSERT 语句执行进 db_path。

    返回 (成功条数, 失败条数, 错误样例 list[str])。
    默认关掉外键约束：样本数据由 LLM 生成，偶有顺序/引用问题，
    关掉约束能最大化恢复行数。需要严格校验时传 foreign_keys=True。
    """
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
    ok = 0
    failed = 0
    errors = []
    try:
        for table_data in sample_data.get("tables", []):
            table_name = table_data.get("table_name", "unknown")
            for stmt in table_data.get("insert_statements", []):
                try:
                    conn.execute(stmt)
                    ok += 1
                except sqlite3.Error as e:
                    failed += 1
                    if len(errors) < 3:
                        errors.append(f"{table_name}: {e}")
        conn.commit()
    finally:
        conn.close()
    return ok, failed, errors


def rebuild_one(schema_rec: dict, sample_rec, db_dir: str, foreign_keys: bool) -> dict:
    """重建单个 .db，返回结果 dict。"""
    scenario = schema_rec.get("scenario", "")
    task_id = schema_rec.get("task_id", "")
    db_filename = f"{safe_dir_name(scenario)}_{task_id}.db"
    db_path = os.path.join(db_dir, db_filename)

    # create_sqlite_database 内部会先删掉同名旧文件，正好覆盖掉 0 字节的坏库
    ok_tables, bad_tables, table_errors = create_sqlite_database(
        db_dir, db_filename, schema_rec.get("db_schema", {})
    )

    inserted = failed = 0
    errors = list(table_errors)
    if sample_rec is None:
        status = "schema_only"
    else:
        inserted, failed, sample_errors = insert_sample_data(
            db_path, sample_rec.get("sample_data", {}), foreign_keys
        )
        errors.extend(sample_errors)
        status = "ok" if failed == 0 and bad_tables == 0 else "partial"

    return {
        "scenario": scenario,
        "task_id": task_id,
        "db_path": db_path,
        "status": status,
        "tables_ok": ok_tables,
        "tables_failed": bad_tables,
        "rows_inserted": inserted,
        "rows_failed": failed,
        "errors": errors,
    }


def rebuild_all(schema_index, sample_index, db_dir, foreign_keys, workers, limit) -> list:
    keys = list(schema_index.keys())
    if limit is not None:
        keys = keys[:limit]

    print(f"[rebuild] 待重建 {len(keys)} 个库 -> {db_dir}")
    print(f"[rebuild] 并发 {workers}，外键约束 {'ON' if foreign_keys else 'OFF'}")

    os.makedirs(db_dir, exist_ok=True)
    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(rebuild_one, schema_index[k], sample_index.get(k), db_dir, foreign_keys): k
            for k in keys
        }
        for fut in as_completed(futures):
            check_cancelled()
            k = futures[fut]
            done += 1
            try:
                results.append(fut.result())
            except Exception as e:  # 单个库炸了不该中断整批
                results.append({
                    "scenario": k[0], "task_id": k[1], "db_path": None,
                    "status": "error", "errors": [f"{type(e).__name__}: {e}"],
                })
            if done % 200 == 0 or done == len(keys):
                print(f"[rebuild] 进度 {done}/{len(keys)}")

    return results


def snapshot_db(db_path: str) -> dict:
    """对 db 里所有表 SELECT *，返回紧凑 JSON 快照 + 行数统计。"""
    conn = sqlite3.connect(db_path)
    conn.text_factory = str  # 文本按 str 处理，避免 bytes 序列化问题
    try:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        snap = {}
        rows_total = 0
        empty_tables = []
        for tb in tables:
            info = conn.execute(f'PRAGMA table_info("{tb}")').fetchall()
            columns = [r[1] for r in info]
            pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
            rows = [list(r) for r in conn.execute(f'SELECT * FROM "{tb}"')]
            snap[tb] = {"columns": columns, "pk": pk, "rows": rows}
            rows_total += len(rows)
            if not rows:
                empty_tables.append(tb)
        return {"snapshot": snap, "tables": len(tables), "rows": rows_total, "empty_tables": empty_tables}
    finally:
        conn.close()


def snapshot_all(db_dir: str, limit, preview: int, preview_chars: int) -> dict:
    db_files = sorted(Path(db_dir).glob("*.db"))
    if limit is not None:
        db_files = db_files[:limit]

    print(f"\n[snapshot] 待统计 {len(db_files)} 个库")
    records = []
    for i, path in enumerate(db_files, 1):
        check_cancelled()
        try:
            info = snapshot_db(str(path))
        except Exception as e:
            print(f"[warn] 读取 {path.name} 失败：{type(e).__name__}: {e}")
            continue
        text = json.dumps(info["snapshot"], ensure_ascii=False, separators=(",", ":"), default=str)
        records.append({
            "db_file": path.name,
            "tables": info["tables"],
            "rows": info["rows"],
            "chars": len(text),
            "utf8_bytes": len(text.encode("utf-8")),
            "empty_tables": info["empty_tables"],
            "snapshot": info["snapshot"],
        })
        if i % 200 == 0 or i == len(db_files):
            print(f"[snapshot] 进度 {i}/{len(db_files)}")

    if not records:
        print("[snapshot] 没有可统计的数据库")
        return {"count": 0}

    chars = [r["chars"] for r in records]
    byts = [r["utf8_bytes"] for r in records]
    rows = [r["rows"] for r in records]
    tabs = [r["tables"] for r in records]

    def stat(arr):
        s = sorted(arr)
        return {
            "count": len(s),
            "total": sum(s),
            "min": s[0],
            "max": s[-1],
            "mean": round(statistics.mean(s), 1),
            "median": s[len(s) // 2],
            "p90": s[min(len(s) - 1, int(0.9 * len(s)))],
            "p99": s[min(len(s) - 1, int(0.99 * len(s)))],
        }

    summary = {
        "db_dir": db_dir,
        "count": len(records),
        "snapshot_chars": stat(chars),
        "snapshot_utf8_bytes": stat(byts),
        "rows_per_db": stat(rows),
        "tables_per_db": stat(tabs),
    }

    print("\n" + "=" * 78)
    print("快照长度统计（紧凑 JSON，行用数组、列名每表出现一次）")
    print("=" * 78)
    for label, key in [("字符数", "snapshot_chars"), ("utf-8 字节数", "snapshot_utf8_bytes")]:
        s = summary[key]
        print(f"\n{label}:")
        print(f"  库数量 : {s['count']}")
        print(f"  最小   : {s['min']:>12,}  ({s['min']/1024:.2f} KB)")
        print(f"  中位数 : {s['median']:>12,}  ({s['median']/1024:.2f} KB)")
        print(f"  平均   : {s['mean']:>12,.1f}  ({s['mean']/1024:.2f} KB)")
        print(f"  p90    : {s['p90']:>12,}  ({s['p90']/1024:.2f} KB)")
        print(f"  p99    : {s['p99']:>12,}  ({s['p99']/1024:.2f} KB)")
        print(f"  最大   : {s['max']:>12,}  ({s['max']/1024:.2f} KB)")
        print(f"  合计   : {s['total']:>12,}  ({s['total']/1024/1024:.2f} MB)")

    print(f"\n每库行数: 中位 {summary['rows_per_db']['median']}, 平均 {summary['rows_per_db']['mean']}, "
          f"最大 {summary['rows_per_db']['max']}")
    print(f"每库表数: 中位 {summary['tables_per_db']['median']}, 平均 {summary['tables_per_db']['mean']}, "
          f"最大 {summary['tables_per_db']['max']}")

    biggest = max(records, key=lambda r: r["chars"])
    smallest = min(records, key=lambda r: r["chars"])
    print(f"\n最大快照: {biggest['db_file']}  {biggest['chars']:,} 字符 / {biggest['rows']} 行 / {biggest['tables']} 表")
    print(f"最小快照: {smallest['db_file']}  {smallest['chars']:,} 字符 / {smallest['rows']} 行 / {smallest['tables']} 表")

    if preview > 0:
        print("\n" + "=" * 78)
        print(f"前 {min(preview, len(records))} 个库的快照")
        print("=" * 78)
        for r in records[:preview]:
            text = json.dumps(r["snapshot"], ensure_ascii=False, indent=2, default=str)
            truncated = preview_chars > 0 and len(text) > preview_chars
            if truncated:
                text = text[:preview_chars] + f"\n... [已截断，全长 {r['chars']:,} 字符]"
            print(f"\n--- {r['db_file']}  |  {r['tables']} 表 / {r['rows']} 行 / "
                  f"{r['chars']:,} 字符 / {r['utf8_bytes']:,} 字节 "
                  f"{'（截断显示）' if truncated else ''} ---")
            print(text)

    return {"summary": summary, "records": records}


def main():
    p = argparse.ArgumentParser(description="重建任务级 .db 并统计数据快照长度")
    p.add_argument("--schema", default=str(DEFAULT_SCHEMA), help="gen_db_ma.jsonl 路径")
    p.add_argument("--sample", default=str(DEFAULT_SAMPLE), help="gen_sample_ma.jsonl 路径")
    p.add_argument("--db-dir", default=str(DEFAULT_DB_DIR), help=".db 存放目录")
    p.add_argument("--limit", type=int, default=None, help="只处理前 N 个任务（调试用）")
    p.add_argument("--workers", type=int, default=8, help="重建并发线程数")
    p.add_argument("--foreign-keys", action="store_true", help="插入时开启外键约束（默认关闭）")
    p.add_argument("--skip-rebuild", action="store_true", help="跳过重建，只做快照统计")
    p.add_argument("--preview", type=int, default=10, help="打印前 N 个快照，0 = 不打印")
    p.add_argument("--preview-chars", type=int, default=0, help="每个快照最多打印字符数，0 = 不截断")
    p.add_argument("--report", default=str(DEFAULT_REPORT), help="统计结果 JSON 输出路径")
    args = p.parse_args()

    for path, label in [(args.schema, "schema"), (args.sample, "sample")]:
        if not os.path.exists(path):
            raise SystemExit(f"[error] {label} 文件不存在：{path}")

    schema_index, schema_total = load_jsonl_by_key(args.schema)
    sample_index, sample_total = load_jsonl_by_key(args.sample)
    print(f"[load] schema {schema_total} 行 -> {len(schema_index)} 个唯一 key")
    print(f"[load] sample {sample_total} 行 -> {len(sample_index)} 个唯一 key")

    matched = sum(1 for k in schema_index if k in sample_index)
    print(f"[load] 两边都能配上的 key: {matched} / {len(schema_index)}")

    if not args.skip_rebuild:
        results = rebuild_all(schema_index, sample_index, args.db_dir,
                              args.foreign_keys, args.workers, args.limit)
        counts = Counter(r["status"] for r in results)
        print("\n[rebuild] 汇总：")
        for status, n in counts.most_common():
            print(f"  {status:<12} {n:>6}")
        print(f"  {'合计':<12} {len(results):>6}")
        bad = [r for r in results if r["status"] in ("partial", "error", "schema_only")]
        if bad:
            print(f"\n[rebuild] 有问题的库（前 10 条）：")
            for r in bad[:10]:
                print(f"  {r.get('db_path') or r['scenario']}  status={r['status']}  "
                      f"inserted={r.get('rows_inserted')} failed={r.get('rows_failed')}  "
                      f"errors={r.get('errors')}")

    result = snapshot_all(args.db_dir, args.limit, args.preview, args.preview_chars)

    if args.report:
        report_dir = os.path.dirname(os.path.abspath(args.report))
        os.makedirs(report_dir, exist_ok=True)
        payload = {
            "summary": result.get("summary", {}),
            # 报告文件里不存完整快照正文，避免几百 MB；需要正文请看 --preview 输出
            "per_db": [
                {k: v for k, v in r.items() if k != "snapshot"}
                for r in result.get("records", [])
            ],
        }
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n[report] 统计结果已写入 {args.report}")


if __name__ == "__main__":
    run_cli(main)
