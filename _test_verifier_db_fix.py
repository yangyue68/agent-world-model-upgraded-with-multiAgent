# -*- coding: utf-8 -*-
"""第七阶段（verifier）回归自测：`_dbs` 清理 + 任务级建库。

设计原则：**不依赖运行期依赖**（loguru / openai / tqdm / fastapi 等都没装也能跑）。
因此不 `import stages.verifier.generate`（它会拉 lib.gpt / lib.orchestrator 等一堆依赖），
而是用 AST 从源文件里抠出被测函数，在「桩命名空间」里 exec 后直接调用。

覆盖：
  A. _cleanup_dbs：clean_db=True 且 _dbs 存在           -> 目录被删掉
  B. _cleanup_dbs：clean_db=False                       -> 目录保留（调试用）
  C. _cleanup_dbs：_dbs 不存在                          -> 不抛异常
  D. 静态一致性（防「只留下调用、没留下定义」）：
     Config 有 clean_db 字段；_cleanup_dbs 有定义；run() 里恰好 3 处调用
     （开跑前清理 / Ctrl+C / finally 收尾）
  E. generate.py 能通过 ast.parse（语法护栏）

运行：`python _test_verifier_db_fix.py`（在本目录 ma_upgrade/ 下）
"""
import ast
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(ROOT, "stages", "verifier", "generate.py")

_RESULTS = []


def _check(name, ok, detail=""):
    _RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))


class _StubLogger:
    """loguru.logger 的桩：吞掉所有日志调用。"""

    def __getattr__(self, _name):
        return lambda *a, **k: None


class _Config:
    """Config 的桩：只承载被 _cleanup_dbs 读取的字段。"""

    def __init__(self, clean_db=True, db_build_dir=""):
        self.clean_db = clean_db
        self.db_build_dir = db_build_dir


def _extract_function(name):
    """从 generate.py 里 AST 抠出单个顶层函数，exec 进桩命名空间后返回。"""
    with open(GEN, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    node = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None
    )
    if node is None:
        raise LookupError(f"generate.py 里找不到函数 {name}")
    ns = {"os": os, "shutil": shutil, "logger": _StubLogger(), "Config": _Config}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), GEN, "exec"), ns)
    return ns[name]


def main():
    # ---------- E. 语法护栏 ----------
    try:
        with open(GEN, encoding="utf-8") as f:
            src = f.read()
        ast.parse(src)
        _check("E. generate.py ast.parse", True)
    except SyntaxError as e:
        _check("E. generate.py ast.parse", False, str(e))
        src = ""

    # ---------- A/B/C. _cleanup_dbs 行为 ----------
    cleanup = _extract_function("_cleanup_dbs")
    tmp = tempfile.mkdtemp(prefix="verifier_dbs_test_")

    # A: clean_db=True -> 删掉
    a_dir = os.path.join(tmp, "_dbs")
    os.makedirs(os.path.join(a_dir, "scene_task_1"), exist_ok=True)
    open(os.path.join(a_dir, "scene_task_1", "scene.db"), "w").close()
    cleanup(_Config(clean_db=True, db_build_dir=a_dir))
    _check("A. clean_db=True 删除 _dbs", not os.path.exists(a_dir))

    # B: clean_db=False -> 保留
    b_dir = os.path.join(tmp, "_dbs_keep")
    os.makedirs(os.path.join(b_dir, "scene_task_1"), exist_ok=True)
    cleanup(_Config(clean_db=False, db_build_dir=b_dir))
    _check("B. clean_db=False 保留 _dbs", os.path.isdir(os.path.join(b_dir, "scene_task_1")))

    # C: 目录不存在 -> 不抛异常
    try:
        cleanup(_Config(clean_db=True, db_build_dir=os.path.join(tmp, "not_exist")))
        ok = True
        detail = ""
    except Exception as e:  # noqa: BLE001
        ok = False
        detail = f"{type(e).__name__}: {e}"
    _check("C. _dbs 不存在时不抛异常", ok, detail)

    shutil.rmtree(tmp, ignore_errors=True)

    # ---------- D. 静态一致性 ----------
    has_field = "clean_db: bool" in src
    _check("D1. Config 有 clean_db 字段", has_field)

    has_def = any(
        isinstance(n, ast.FunctionDef) and n.name == "_cleanup_dbs" for n in ast.parse(src).body
    ) if src else False
    _check("D2. _cleanup_dbs 有函数定义", has_def)

    call_sites = src.count("_cleanup_dbs(config,")
    _check("D3. run() 里恰好 3 处 _cleanup_dbs 调用", call_sites == 3, f"实际 {call_sites} 处")

    ok_all = all(ok for _, ok, _ in _RESULTS)
    print("-" * 60)
    print(f"结果：{sum(1 for _, ok, _ in _RESULTS if ok)}/{len(_RESULTS)} 通过")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
