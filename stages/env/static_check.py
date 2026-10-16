"""env 阶段的多文件静态检查（纯确定性，不调用 LLM）。

存在的原因：原先只有「语法 + model 列名」两类校验，跨文件符号不一致
（import 不存在的名字、response_model 指向 ORM 类、缺 database.py、漏 import datetime）
都要等真的起一个 uvicorn 服务器、等它崩、把 traceback 喂给模型才能发现。而这些错误
完全可以在起进程之前静态检出，或者由 runner 的确定性补丁直接修掉。

两个入口：
  - analyze_files(files)：纯 AST 跨文件检查，零成本，返回问题列表；
  - run_import_check(files, db_path)：子进程里只 "import main"，捕获全部导入期错误。

导入期检查无需等待端口或发 HTTP 请求，可以捕获 NameError、ModuleNotFoundError、
ImportError 和装饰器注册时的 FastAPIError。它不能证明接口业务逻辑正确。
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time

from lib.interrupt import check_cancelled, interruptible_sleep, managed_popen, release_process


# 常见的「应该 import 却没有」的名字，分两组：SQLAlchemy 符号、datetime 类型。
SQLALCHEMY_NAMES = {
    "Column", "DateTime", "Date", "Time", "String", "Text", "Integer", "BigInteger",
    "SmallInteger", "Float", "Numeric", "Boolean", "JSON", "LargeBinary", "ForeignKey",
    "UniqueConstraint", "CheckConstraint", "Index", "PrimaryKeyConstraint",
    "func", "and_", "or_", "not_", "desc", "asc", "case", "cast",
}
DATETIME_NAMES = {"datetime", "date", "timedelta", "timezone"}
SQLALCHEMY_TYPES = {"DateTime", "Date", "Time"}

# 标准库 / typing 里最常漏的 import（日志里 database.py 漏 os、routers 漏 List）。
# COMMON_IMPORTS 是 {名字: import 语句}；COMMON_NAMES / TYPING_NAMES 是名字集合。
COMMON_IMPORTS = {"os": "import os", "sys": "import sys", "json": "import json", "re": "import re"}
COMMON_NAMES = set(COMMON_IMPORTS)
TYPING_NAMES = {"List", "Dict", "Optional", "Any", "Tuple"}

# 顶层模块名：这些必须真实存在，且内部只允许互相绝对 import。
TOP_LEVEL_MODULES = ("database", "models", "schemas")


def _strip_fences(code: str) -> str:
    code = (code or "").strip()
    if code.startswith("```"):
        lines = code.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines).strip()
    return code


def _normalized(path: str) -> str:
    return path.strip() if isinstance(path, str) else ""


def _module_name(path: str) -> str:
    """'routers/users.py' -> 'routers.users'；'models.py' -> 'models'。"""
    p = _normalized(path)
    if p.endswith(".py"):
        p = p[:-3]
    if p.endswith("/__init__"):
        p = p[:-len("/__init__")]
    return p.replace("/", ".")


def _parse(files):
    """{规范化路径: (原始条目, AST 或 None)}；语法错误的文件的 AST 为 None。"""
    out = {}
    for f in files or []:
        if not isinstance(f, dict):
            continue
        path = _normalized(f.get("path", ""))
        if not path:
            continue
        try:
            tree = ast.parse(_strip_fences(f.get("content", "")))
        except (SyntaxError, TypeError, AttributeError):
            tree = None  # 语法错误由 validate_codegen_output 报告，这里不重复报
        out[path] = (f, tree)
    return out


def _walk_bodies(body):
    """展开 module / try / if 的 body，让 try-except 里的 import 也算可见。"""
    for node in body:
        yield node
        if isinstance(node, (ast.Try, ast.If)):
            yield from _walk_bodies(node.body)
            yield from _walk_bodies(getattr(node, "orelse", []) or [])
            for handler in getattr(node, "handlers", []) or []:
                yield from _walk_bodies(handler.body)


def _bound_names(tree):
    """文件里所有「被绑定」的名字（任何作用域），用来避免误报未 import。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Global):
            names.update(node.names)
    return names


def _used_names(tree):
    """所有被读取的名字。"""
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _module_visible_names(tree):
    """模块级可见的名字：类/函数/赋值/import，含 try/except、if 块内。"""
    names = set()
    for node in _walk_bodies(tree.body):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _resolve_import_module(path, node):
    if not node.level:
        return node.module or ""
    package = _module_name(path).split(".")
    if not path.endswith("/__init__.py"):
        package = package[:-1]
    if node.level > len(package):
        return None
    parent = package[:len(package) - node.level + 1]
    return ".".join(parent + ([node.module] if node.module else []))


def _check_imports(path, tree, trees):
    """检查 from X import a：X 是包内模块时，a 是否真的存在。"""
    problems = []
    module_paths = {_module_name(p): p for p in trees}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        mod = _resolve_import_module(path, node)
        level = node.level or 0

        if level > 0 and mod not in module_paths:
            problems.append(
                f"{path}: 使用了相对 import（from {'.' * level}{mod} import ...）；"
                "该相对路径无法解析到代码包内模块；引用顶层模块请改成绝对 import"
            )
            continue

        # 只检查包内模块之间的互相引用；第三方 import（fastapi/sqlalchemy）不查
        if mod not in module_paths:
            continue

        target_path = module_paths[mod]
        entry = trees.get(target_path)
        target_tree = entry[1] if entry else None
        if target_tree is None:
            continue  # 目标不存在由 _check_missing_modules 报；语法错误由语法校验报
        visible = _module_visible_names(target_tree)

        for alias in node.names:
            if alias.name == "*" or alias.name in visible:
                continue
            # from routers import flights：flights 是子模块而不是符号
            child = mod.replace(".", "/") + "/" + alias.name
            if child + ".py" in trees or child + "/__init__.py" in trees:
                continue
            problems.append(f"{path}: from {mod} import {alias.name} 失败——{target_path} 里没有定义 {alias.name}")
    return problems


def _check_missing_modules(trees):
    """被 import 的顶层模块（database/models/schemas）必须真实存在。"""
    problems = []
    importers = {}
    for path, (_, tree) in trees.items():
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and not node.level and node.module:
                importers.setdefault(node.module.split(".")[0], set()).add(path)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    importers.setdefault(alias.name.split(".")[0], set()).add(path)

    for mod, users in importers.items():
        if mod in TOP_LEVEL_MODULES and f"{mod}.py" not in trees:
            who = "、".join(sorted(users))
            problems.append(f"缺少 {mod}.py，但 {who} 都在 import 它；请在文件规划里补上该模块")
    return problems


def _orm_class_names(trees):
    """models.py 里带 __tablename__ 的类名（= ORM 类）。"""
    names = set()
    for path, (_, tree) in trees.items():
        if tree is None or not path.endswith("models.py"):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(
                    getattr(t, "id", None) == "__tablename__" for t in stmt.targets
                ):
                    names.add(node.name)
                    break
    return names


def _check_response_model(trees):
    """response_model= 不能直接指向 ORM 类（会触发 FastAPIError: Invalid args for response field）。"""
    orm_classes = _orm_class_names(trees)
    if not orm_classes:
        return []
    problems = []
    for path, (_, tree) in trees.items():
        if tree is None:
            continue
        orm_bindings = set()
        module_bindings = set()
        if path.endswith("models.py"):
            orm_bindings.update(orm_classes)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = _resolve_import_module(path, node) or ""
                if mod == "models" or mod.endswith(".models"):
                    for alias in node.names:
                        if alias.name in orm_classes:
                            orm_bindings.add(alias.asname or alias.name)
                        elif alias.name == "*":
                            orm_bindings.update(orm_classes)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "models":
                        module_bindings.add(alias.asname or alias.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "response_model":
                    for value in ast.walk(kw.value):
                        is_orm = isinstance(value, ast.Name) and value.id in orm_bindings
                        is_orm |= (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                                   and value.value.id in module_bindings and value.attr in orm_classes)
                        if not is_orm:
                            continue
                        problems.append(
                            f"{path}: response_model={ast.unparse(kw.value)} 直接用了 ORM 类；"
                            "必须改成 schemas.py 里的 XxxOut 响应模型"
                        )
    return problems


def _check_missing_imports(trees):
    """用到 SQLAlchemy / datetime 的常见名字，却在本文件里完全没有绑定。"""
    problems = []
    for path, (_, tree) in trees.items():
        if tree is None:
            continue
        missing = sorted((_used_names(tree) & SQLALCHEMY_NAMES) - _bound_names(tree))
        missing_dt = sorted((_used_names(tree) & DATETIME_NAMES) - _bound_names(tree))
        missing_typing = sorted((_used_names(tree) & TYPING_NAMES) - _bound_names(tree))
        missing_common = sorted((_used_names(tree) & COMMON_NAMES) - _bound_names(tree))
        if missing:
            problems.append(f"{path}: 使用了未 import 的名字 {missing}（应 from sqlalchemy import ...）")
        if missing_dt:
            problems.append(
                f"{path}: 使用了未 import 的名字 {missing_dt}（应 from datetime import ...）"
            )
        if missing_typing:
            problems.append(f"{path}: 使用了未 import 的名字 {missing_typing}（应 from typing import ...）")
        if missing_common:
            problems.append(
                f"{path}: 使用了未 import 的名字 {missing_common}（应 {' / '.join(COMMON_IMPORTS[n] for n in missing_common)}）"
            )
    return problems


def _check_bad_column_kwargs(trees):
    """Column(...) 不接受 check_constraint / unique_index 这类 Django 风格的约束参数。

    SQLAlchemy 2.x 会为它们发 SAWarning 并**静默忽略**，模型以为自己加了约束、
    实际没有，属于日志里真实出现过的错误。正确写法是 CheckConstraint(...) 或
    直接把约束写进 DDL。
    """
    problems = []
    bad = {"check_constraint", "unique_index", "db_index_comment", "verbose_name"}
    for path, (_, tree) in trees.items():
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "Column"):
                continue
            for kw in node.keywords:
                if kw.arg in bad:
                    problems.append(
                        f"{path}: Column(..., {kw.arg}=...) 不是 SQLAlchemy 的参数"
                        f"（会触发 SAWarning 并被静默忽略）；"
                        f"改用 CheckConstraint / UniqueConstraint，或写进 DDL"
                    )
    return problems


def _check_django_datetime(trees):
    """抓 Django 习惯误用：Column(Date, default=Date.today()) / DateTime.now()。"""
    problems = []
    for path, (_, tree) in trees.items():
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr in {"today", "now", "utcnow"}
                    and isinstance(node.value, ast.Name) and node.value.id in SQLALCHEMY_TYPES):
                problems.append(
                    f"{path}: {node.value.id}.{node.attr} 不存在（Django 写法；SQLAlchemy 列类型不是日期对象）；"
                    "SQLAlchemy 里请改用 datetime 模块，例如 from datetime import datetime, date "
                    "后写 datetime.now() / date.today()"
                )
    return problems


def analyze_files(files):
    """对整个代码包做跨文件静态检查，返回问题列表（空列表 = 通过）。

    语法错误不在这里报（由 validate_codegen_output 负责），本函数只报「语法没问题
    但跑不起来」这一类问题。
    """
    trees = _parse(files)
    if not trees:
        return []
    problems = []
    problems += _check_missing_modules(trees)
    problems += _check_response_model(trees)
    problems += _check_missing_imports(trees)
    problems += _check_bad_column_kwargs(trees)
    problems += _check_django_datetime(trees)
    for path, (_, tree) in trees.items():
        if tree is not None:
            problems += _check_imports(path, tree, trees)
    seen = set()
    unique = []
    for problem in problems:
        if problem not in seen:
            seen.add(problem)
            unique.append(problem)
    return unique


def run_import_check(files, db_path, timeout=25.0):
    """在临时目录里只执行 "import main"，捕获全部导入期错误。

    比启动 uvicorn 快得多，而且能覆盖「服务器一起来就崩」的全部原因：缺 import、
    名字不存在、ORM 类当 response_model、FastAPI 装饰器期报错、建表 DDL 不合法。
    main.py 里的 uvicorn.run 在 __name__ == "__main__" 分支，import 时不会启动服务器。

    返回 (ok, output)：ok=False 时 output 是子进程输出，可直接喂给 fixer。
    """
    from stages.env.runner import _kill, write_files_to_dir

    temp_dir = tempfile.mkdtemp(prefix="env_import_")
    proc = None
    logfile = None
    try:
        write_files_to_dir(files, temp_dir, patch_db=True)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if db_path and os.path.exists(db_path):
            temp_db = os.path.join(temp_dir, "import_check.db")
            shutil.copyfile(db_path, temp_db)
            env["DATABASE_PATH"] = "sqlite:///" + temp_db.replace(chr(92), "/")

        logfile = tempfile.TemporaryFile()
        proc = managed_popen(
            [sys.executable, "-c", "from main import app; from database import Base; app.openapi()"],
            cwd=temp_dir,
            stdout=logfile,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while proc.poll() is None and time.monotonic() < deadline:
            interruptible_sleep(0.1)
        check_cancelled()
        timed_out = proc.poll() is None
        if timed_out:
            _kill(proc)
        logfile.seek(0)
        stdout = logfile.read().decode("utf-8", errors="replace")
        if timed_out:
            return False, f"import main 超时（{timeout:.0f}s），模块顶层可能有阻塞操作\n{stdout}"
        return proc.returncode == 0, stdout or f"import main 退出码 {proc.returncode}"
    except Exception as exc:
        return False, f"import 检查自身异常：{type(exc).__name__}: {exc}"
    finally:
        if proc is not None and proc.poll() is None:
            _kill(proc)
        if proc is not None:
            release_process(proc)
        if logfile is not None:
            logfile.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
