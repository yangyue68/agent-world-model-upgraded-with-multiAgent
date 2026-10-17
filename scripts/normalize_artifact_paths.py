# -*- coding: utf-8 -*-
"""把产物里残留的绝对路径批量改回相对路径（跨平台迁移修复脚本）。

背景
----
产物（output/ 下的 *.jsonl / *.json）会在 Windows 开发机和 Linux 训练服务器之间
来回拷贝。历史版本的若干阶段把 db_path / env_dir 等字段写成了**当时的本机绝对
路径**，例如：

    "db_path": "E:\\论文阅读合集\\...\\ma_upgrade\\output\\db\\databases\\Amazon_task_2.db"

这种值拷到 Linux 上就是定时炸弹：``os.path.isabs("E:\\...")`` 在 Linux 返回 False，
于是它被当成相对路径拼到 CWD 上，得到

    /root/autodl-tmp/<repo>/E:/论文阅读合集/.../Amazon_task_2.db

一个永远不存在的垃圾路径。RL 阶段的 copy_database 正是这样整批 FileNotFoundError，
表现为「verifier 里同一个 order_id 能查到、RL rollout 里却 404」——因为 rollout
根本没打开正确的数据库。

写入侧已经修好（各阶段统一用 portable_project_path 落盘相对路径），本脚本负责
清理**已经生成**的产物。

策略
----
- Windows 盘符路径（``E:\\...``、``D:/...``）：**任何字段**都改。业务数据几乎不可能
  含盘符，出现即污染。
- POSIX 绝对路径（``/root/...``）：**只改已知路径字段**（db_path、env_dir 等），
  避免误伤数据库快照里恰好以 / 开头的普通业务值，也避免误伤
  ``/data/models/Qwen3-4B`` 这类合法的外部资源路径。

用法（在 ma_upgrade 的上级目录，或 ma_upgrade 目录内）
------------------------------------------------------
  python -m ma_upgrade.scripts.normalize_artifact_paths              # 只预览，不改动
  python -m ma_upgrade.scripts.normalize_artifact_paths --apply      # 实际改写（自动备份）
  python -m ma_upgrade.scripts.normalize_artifact_paths --apply --no-backup
  python -m ma_upgrade.scripts.normalize_artifact_paths --path output/sample --apply
  python -m ma_upgrade.scripts.normalize_artifact_paths --show 5     # 每个文件多打印几条样例
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


_MA_ROOT = Path(__file__).resolve().parent.parent          # ma_upgrade/
_PROJECT_ROOT = _MA_ROOT.parent                            # 部署根目录
for _p in (str(_MA_ROOT), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.tools import portable_project_path  # noqa: E402


_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# 只有这些字段里的 POSIX 绝对路径才会被改写（盘符路径不受此限制）。
PATH_KEYS = {
    "db_path", "env_dir", "traj_dir", "env_path", "code_path",
    "trajectory_dir", "trajectory_path", "initial_snapshot_source",
    "judge_input_path", "judge_result_path", "judge_evidence_dir",
    "source_db", "final_db", "initial_db", "initial_db_path", "final_db_path",
    "initial_db_source", "output_dir", "envs_dir", "trajectory_root",
    "evidence_dir", "database_dir", "adapter_path", "checkpoint_path",
    "bad_tasks_path", "run_dir", "work_dir", "spec_path",
    "input_task", "input_db", "input_sample", "input_spec", "input_env",
    "sample_path", "tasks_path", "verifier_path", "envs_path", "allowed_tasks_path",
}

DEFAULT_TARGET = _MA_ROOT / "output"
_SUFFIXES = (".jsonl", ".json")

# 这些字段装的是**业务数据**而非产物路径，整棵子树都跳过：
#   - initial_snapshot / sample_data / db_schema：数据库快照、INSERT、DDL，
#     里面的字符串是表数据，可能恰好以 / 开头（如 file_path 列的值）。
#   - judge_result / verification / task / reasoning：LLM 生成的文本与代码，
#     里面出现的路径只是叙述，改了反而破坏证据链。
# 注意只跳过子树，同名顶层字段（如 judge_result.json 里的 db_path）照常处理。
SKIP_SUBTREES = {
    "initial_snapshot", "sample_data", "db_schema", "api_spec",
    "judge_result", "verification", "task", "tasks", "reasoning",
    "evidence", "insert_statements", "tables", "ddl", "indexes",
}


def _needs_fix(value: str, key: str) -> bool:
    """判断一个字符串值是否需要被规范化成相对路径。"""
    text = value.strip()
    if len(text) < 3:
        return False
    # 盘符路径：跨平台污染，任何字段都改。
    if _WINDOWS_DRIVE_RE.match(text):
        return True
    # POSIX 绝对路径：只在已知路径字段上改，保护业务数据和外部资源路径。
    if text.startswith("/") and str(key).lower() in PATH_KEYS:
        return True
    return False


def _to_relative(value: str) -> str:
    """把绝对路径转成相对 ma_upgrade 的 POSIX 路径。"""
    return portable_project_path(value.strip(), _MA_ROOT)


def _normalize(node, fixes: list, key: str = "", path: str = "$"):
    """递归遍历 JSON 结构，返回规范化后的副本（不改原对象）。"""
    if isinstance(node, dict):
        return {
            k: (v if str(k).lower() in SKIP_SUBTREES
                else _normalize(v, fixes, str(k), f"{path}.{k}"))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_normalize(v, fixes, key, f"{path}[{i}]") for i, v in enumerate(node)]
    if isinstance(node, str) and _needs_fix(node, key):
        new = _to_relative(node)
        if new and new != node:
            fixes.append((path, node, new))
            return new
    return node


def iter_targets(target: Path):
    """列出待处理的产物文件（跳过本脚本自己生成的备份）。"""
    if target.is_file():
        yield target
        return
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _SUFFIXES:
            continue
        if ".bak_" in path.name or path.name.endswith(".bak"):
            continue
        if path.name.endswith(".tmp"):
            continue
        yield path


def _backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(path.name + f".bak_{stamp}")
    shutil.copy2(path, backup)
    return backup


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _display(path: Path) -> str:
    for base in (_MA_ROOT, _PROJECT_ROOT):
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return str(path)


def process_file(path: Path, args, stats: dict) -> list:
    """处理单个产物文件，返回本文件内的修改清单 [(字段路径, 旧值, 新值)]。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        stats["unreadable"] += 1
        print(f"[skip] 无法读取 {_display(path)}: {exc}")
        return []

    fixes: list = []
    trailing_nl = raw.endswith("\n")

    if path.suffix.lower() == ".jsonl":
        new_lines = []
        broken = 0
        for line in raw.splitlines():
            if not line.strip():
                new_lines.append(line)
                continue
            try:
                record = json.loads(line)
            except Exception:
                broken += 1
                new_lines.append(line)          # 原样保留，绝不猜着改
                continue
            new_lines.append(
                json.dumps(_normalize(record, fixes), ensure_ascii=False, default=str))
        if broken:
            # 有解析不了的行就不整体重写：宁可漏改，也不能把坏行附近的合法数据搞乱。
            stats["broken_files"] += 1
            print(f"[warn] {_display(path)} 有 {broken} 行无法解析 JSON，已跳过不写入")
            return fixes
        new_text = "\n".join(new_lines) + ("\n" if trailing_nl else "")
    else:
        try:
            document = json.loads(raw)
        except Exception as exc:
            stats["broken_files"] += 1
            print(f"[warn] {_display(path)} 不是合法 JSON，已跳过：{exc}")
            return fixes
        # judge_result.json 这类文件原本是 indent=2 落盘的，保持一致以免产生巨量无意义 diff。
        new_text = json.dumps(_normalize(document, fixes),
                              ensure_ascii=False, indent=2, default=str)
        if trailing_nl:
            new_text += "\n"

    if not fixes:
        return fixes

    stats["files_changed"] += 1
    stats["values_changed"] += len(fixes)
    if args.apply:
        if not args.no_backup:
            stats["backups"] += 1
            _backup(path)
        _atomic_write(path, new_text)
    return fixes


def main():
    parser = argparse.ArgumentParser(
        description="把产物里残留的绝对路径批量改回相对路径（跨平台迁移修复）")
    parser.add_argument("--path", default=str(DEFAULT_TARGET),
                        help="要处理的文件或目录（默认 ma_upgrade/output）")
    parser.add_argument("--apply", action="store_true",
                        help="实际改写文件；不加此参数只预览，不动任何文件")
    parser.add_argument("--no-backup", action="store_true",
                        help="改写时不生成 .bak_<时间戳> 备份")
    parser.add_argument("--show", type=int, default=3,
                        help="每个文件最多打印几条修改样例，0 = 不打印")
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        # 允许传相对 ma_upgrade / 部署根目录 / 当前工作目录的路径
        for base in (_MA_ROOT, _PROJECT_ROOT, Path.cwd()):
            candidate = base / args.path
            if candidate.exists():
                target = candidate
                break
    if not target.exists():
        raise SystemExit(f"[error] 目标不存在：{args.path}")

    stats = {"scanned": 0, "files_changed": 0, "values_changed": 0,
             "backups": 0, "broken_files": 0, "unreadable": 0}

    print("=" * 78)
    print(f"相对路径基准（ma_upgrade）: {_MA_ROOT}")
    print(f"扫描目标                  : {target}")
    print(f"运行模式                  : {'实际改写' if args.apply else '预览（不会改动任何文件）'}")
    if args.apply and not args.no_backup:
        print("备份策略                  : 每个被改写的文件生成 <name>.bak_<时间戳>")
    print("=" * 78)

    for path in iter_targets(target):
        stats["scanned"] += 1
        fixes = process_file(path, args, stats)
        if fixes and args.show > 0:
            print(f"\n{_display(path)}  （{len(fixes)} 处）")
            for field, old, new in fixes[: args.show]:
                print(f"  {field}")
                print(f"    - {old}")
                print(f"    + {new}")
            if len(fixes) > args.show:
                print(f"  ... 其余 {len(fixes) - args.show} 处省略")

    print("\n" + "=" * 78)
    print("汇总")
    print(f"  扫描文件          : {stats['scanned']}")
    print(f"  含绝对路径的文件  : {stats['files_changed']}")
    print(f"  需修改的路径值    : {stats['values_changed']}")
    if stats["broken_files"]:
        print(f"  解析失败已跳过    : {stats['broken_files']}")
    if stats["unreadable"]:
        print(f"  无法读取已跳过    : {stats['unreadable']}")
    if args.apply:
        print(f"  已生成备份        : {stats['backups']}")
        print("\n改写完成。建议先用小样本验证 rollout 能正常起环境，再跑全量训练。")
    elif stats["values_changed"]:
        print("\n以上为预览结果，未改动任何文件。确认无误后加 --apply 执行：")
        print("  python -m ma_upgrade.scripts.normalize_artifact_paths --apply")
    else:
        print("\n没有发现需要修复的绝对路径，产物是干净的。")
    print("=" * 78)


if __name__ == "__main__":
    main()

