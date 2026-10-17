# -*- coding: utf-8 -*-
"""验证跨平台路径修复是否生效，并扫描产物里还剩多少绝对路径。

第一部分是一组**纯逻辑断言**：用临时目录伪造一个 ma_upgrade 项目根，把历史上
真实出现过的各种脏路径形态喂给 resolve_project_path / portable_project_path，
确认它们都被正确重定位。不需要跑训练、不需要产物齐全、不需要 GPU，几秒出结果。

第二部分是**产物扫描**：统计 output/ 下还有多少个字段存着绝对路径，用来确认
normalize_artifact_paths 是否已经清理干净。

用法：
  python -m ma_upgrade.scripts.verify_path_fix
  python ma_upgrade/scripts/verify_path_fix.py
  python -m ma_upgrade.scripts.verify_path_fix --no-scan     # 只跑断言

退出码 0 = 全部通过；1 = 有断言失败（说明修复没生效，别去跑训练）。
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

_MA_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _MA_ROOT.parent
for _p in (str(_MA_ROOT), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.tools import portable_project_path, resolve_project_path  # noqa: E402


# 每条用例：(说明, 输入路径, 期望的相对 ma_upgrade 结果)
# 输入覆盖了实际事故里出现过的全部形态，尤其是那条把 RL 打挂的 Windows 路径。
RESOLVE_CASES = [
    (
        "期望形态：本来就是相对路径",
        "output/db/databases/Amazon_task_2.db",
        "output/db/databases/Amazon_task_2.db",
    ),
    (
        "事故现场：Windows 路径 + 混用分隔符（含 ma_upgrade 段）",
        "E:\\论文阅读合集\\agent-world-model-main\\agent-world-model-main"
        "\\ma_upgrade\\output/db/databases/Amazon_task_2.db",
        "output/db/databases/Amazon_task_2.db",
    ),
    (
        "同上，但全部是反斜杠",
        "E:\\论文阅读合集\\awm\\awm\\ma_upgrade\\output\\db\\databases\\Amazon_task_2.db",
        "output/db/databases/Amazon_task_2.db",
    ),
    (
        "同上，但全部是正斜杠",
        "E:/论文阅读合集/awm/awm/ma_upgrade/output/db/databases/Amazon_task_2.db",
        "output/db/databases/Amazon_task_2.db",
    ),
    (
        "老版本产物：Windows 路径里没有 ma_upgrade 段（只靠 output 锚点）",
        "E:\\论文阅读合集\\awm\\awm\\output\\db\\databases\\Amazon_task_2.db",
        "output/db/databases/Amazon_task_2.db",
    ),
    (
        "另一台 Linux 机器的绝对路径",
        "/root/autodl-tmp/agent-world-model-upgraded-with-multiAgent"
        "/ma_upgrade/output/db/databases/Amazon_task_2.db",
        "output/db/databases/Amazon_task_2.db",
    ),
    (
        "env_dir 目录路径同样要能重定位",
        "E:\\论文阅读合集\\awm\\ma_upgrade\\output\\environment\\envs\\Amazon_task_2",
        "output/environment/envs/Amazon_task_2",
    ),
    (
        "带 ./ 前缀的相对路径",
        "./output/db/databases/Amazon_task_2.db",
        "output/db/databases/Amazon_task_2.db",
    ),
]

# 这些输入**不该**被挂进项目目录（外部资源），必须原样保留。
PRESERVE_CASES = [
    ("模型权重等外部资源", "/data/models/Qwen3-4B"),
    ("魔搭缓存目录", "/root/.cache/modelscope/hub/Qwen/Qwen3-4B"),
]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def run_assertions() -> int:
    """跑全部断言，返回失败条数。"""
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "ma_upgrade"
        (root / "output" / "db" / "databases").mkdir(parents=True)

        print("=" * 78)
        print(f"伪造项目根: {root}")
        print("=" * 78)

        def check(section_ok, title, raw, expected, got):
            nonlocal failures
            if not section_ok:
                failures += 1
            print(f"  {'PASS' if section_ok else 'FAIL'}  {title}")
            if not section_ok:
                print(f"        输入  : {raw}")
                print(f"        期望  : {expected}")
                print(f"        实际  : {got}")

        print("\n[1] resolve_project_path：脏路径必须重定位到当前项目根")
        for title, raw, expected in RESOLVE_CASES:
            got = _rel(resolve_project_path(raw, root), root)
            check(got == expected, title, raw, expected, got)

        print("\n[2] resolve_project_path：外部资源路径不得被挂进项目根")
        for title, raw in PRESERVE_CASES:
            got = resolve_project_path(raw, root)
            # 用「root 不在 got 的父链里」判定，跨平台都成立：Windows 上
            # Path('/data/x').resolve() 会补成 E:\\data\\x，直接比字符串会误判。
            outside = root not in got.parents and got != root
            check(outside, title, raw, "保持在项目外", str(got))

        print("\n[3] portable_project_path：写进产物的值必须是相对路径")
        for title, raw, expected in RESOLVE_CASES:
            got = portable_project_path(raw, root)
            relative = not got.startswith("/") and ":" not in got
            check(got == expected and relative, title, raw, expected, got)

        print("\n[4] portable_project_path：任何输入都不得产出绝对路径")
        hostile = [
            "E:\\论文阅读合集\\awm\\ma_upgrade\\output\\db\\X.db",
            "/root/autodl-tmp/other/ma_upgrade/output/db/X.db",
            "/data/models/Qwen3-4B",
            "D:/somewhere/else/output/db/X.db",
            "output/db/X.db",
        ]
        for raw in hostile:
            got = portable_project_path(raw, root)
            ok = not got.startswith("/") and ":" not in got
            check(ok, f"{raw}  ->  {got}", raw, "相对路径", got)

    return failures


def scan_artifacts() -> int:
    """扫描产物，报告还剩多少绝对路径字段。"""
    try:
        from scripts.normalize_artifact_paths import (DEFAULT_TARGET, _needs_fix,
                                                      iter_targets)
    except ImportError:                      # 直接 python scripts/xxx.py 运行时
        from normalize_artifact_paths import DEFAULT_TARGET, _needs_fix, iter_targets

    print("\n" + "=" * 78)
    print(f"产物扫描: {DEFAULT_TARGET}")
    print("=" * 78)
    if not DEFAULT_TARGET.exists():
        print("  output/ 目录不存在，跳过扫描")
        return 0

    state = {"files": 0, "values": 0}
    samples = []

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and _needs_fix(node, key):
            state["values"] += 1
            if len(samples) < 10:
                samples.append((key, node))

    for path in iter_targets(DEFAULT_TARGET):
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        before = state["values"]
        if path.suffix.lower() == ".jsonl":
            for line in raw.splitlines():
                if line.strip():
                    try:
                        walk(json.loads(line))
                    except Exception:
                        continue
        else:
            try:
                walk(json.loads(raw))
            except Exception:
                continue
        if state["values"] > before:
            state["files"] += 1
            print(f"  {_rel(path, _MA_ROOT)}: {state['values'] - before} 处")

    if state["values"]:
        print(f"\n  合计 {state['files']} 个文件、{state['values']} 个字段仍是绝对路径。样例：")
        for key, value in samples:
            print(f"    {key} = {value[:120]}")
        print("\n  请执行清理：")
        print("    python -m ma_upgrade.scripts.normalize_artifact_paths          # 先预览")
        print("    python -m ma_upgrade.scripts.normalize_artifact_paths --apply  # 再改写")
    else:
        print("\n  产物是干净的，没有残留绝对路径。")
    return state["values"]


def main():
    parser = argparse.ArgumentParser(description="验证跨平台路径修复")
    parser.add_argument("--no-scan", action="store_true", help="只跑断言，不扫描产物")
    args = parser.parse_args()

    failures = run_assertions()
    dirty = 0 if args.no_scan else scan_artifacts()

    print("\n" + "=" * 78)
    if failures:
        print(f"结果：{failures} 条断言失败 —— 路径修复未生效，请先修好再跑训练。")
    else:
        print("结果：全部断言通过，路径层已能正确处理跨平台污染。")
        if dirty:
            print(f"但产物里仍有 {dirty} 个绝对路径字段待清理（见上方提示）。")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

