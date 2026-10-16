# -*- coding: utf-8 -*-
"""一次性脚本：把现有 gen_sample_ma.jsonl 里每个场景的 sample_data.tables 按外键拓扑重排序并写回。

背景：sample 阶段「执行时」按外键拓扑排序（execute_inserts 里 _order_tables_by_fk），
但「保存产物」时保存的是 generator 原始顺序（未排序）。下游 rl 用 execute_sample_data
按原始顺序执行，遇到子表先插、父表主键还没插入时会报 IntegrityError。

本脚本把已有产物的 tables 顺序重排成「父表在前」，让下游按原始顺序执行也安全。
（新产物已由 generate.py 的方案 A 修复，本脚本用于处理历史产物。）

用法（在 ma_upgrade 目录下）：
  python -m stages.sample.reorder_sample_by_fk
"""

import json
import os
import sys
from pathlib import Path

# 定位 ma_upgrade（reorder_sample_by_fk.py 上溯三级：sample → stages → ma_upgrade）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from lib.interrupt import run_cli, check_cancelled
from lib.tools import tools_jsonl_load, normalize_scenario_name
from stages.sample.generate import _order_tables_by_fk  # 复用外键拓扑排序（与 sample 阶段执行时一致）

_SAMPLE_PATH = str(_PROJECT_ROOT / "stages" / "sample" / "output" / "gen_sample_ma.jsonl")
_DB_PATH = str(_PROJECT_ROOT / "stages" / "db" / "output" / "gen_db_ma.jsonl")


def run(sample_path: str = _SAMPLE_PATH, db_path: str = _DB_PATH):
    if not os.path.exists(sample_path):
        raise FileNotFoundError(f"sample 产物不存在：{sample_path}")
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"db 产物不存在：{db_path}")

    samples = tools_jsonl_load(sample_path)
    db_map = {normalize_scenario_name(d["scenario"]): d.get("db_schema", {}) for d in tools_jsonl_load(db_path)}

    reordered = 0
    skipped = 0
    for s in samples:
        check_cancelled()
        key = normalize_scenario_name(s.get("scenario", ""))
        db_schema = db_map.get(key)
        if not db_schema or not db_schema.get("tables"):
            skipped += 1
            continue
        s["sample_data"] = _order_tables_by_fk(s.get("sample_data", {"tables": []}), db_schema)
        reordered += 1

    # 写回（覆盖原文件）
    with open(sample_path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"重排序 {reordered} 个场景；跳过（无 db_schema）{skipped} 个")
    print(f"已写回 {sample_path}")


def main():
    run()


if __name__ == "__main__":
    run_cli(main)
