"""env 阶段（多文件环境代码）的执行器：落盘 + 多文件启动测试 + endpoint 冒烟测试。

不修改原项目的 test_run_specific_env（它是单文件假设），这里自己实现多文件启动逻辑：
  - 把 files（[{path, content}]）落盘到临时目录（保持目录结构，os.makedirs 自动建目录）；
  - 复制 .db 到临时目录，设置 DATABASE_PATH / HOST / PORT 环境变量；
  - python main.py 起服务器，轮询端口直到就绪；
  - 启动成功后用 httpx 请求 /openapi.json 拿所有无路径参数的 GET endpoint，逐个 GET 检测 5xx。
"""

import ast
import os
import re
import sys
import time
import uuid
import shutil
import tempfile
import subprocess

from loguru import logger

from lib.tools import get_random_available_port, wait_port_free
from lib.interrupt import check_cancelled, interruptible_sleep, managed_popen, release_process


def strip_code_fences(code: str) -> str:
    """去掉 LLM 可能包在代码外面的 markdown 代码围栏（```python ... ```）。"""
    code = code.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines).strip()
    return code


def _insert_import(tree, statement):
    """保留模块文档字符串和 __future__ import 的位置。"""
    index = 0
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        index = 1
    while (index < len(tree.body) and isinstance(tree.body[index], ast.ImportFrom)
           and tree.body[index].module == "__future__"):
        index += 1
    tree.body.insert(index, ast.parse(statement).body[0])


def _patch_engine(content: str) -> str:
    """把硬编码的 create_engine('sqlite:///...') 换成读 DATABASE_PATH 环境变量。

    这样测试时只需设置 DATABASE_PATH 环境变量即可连上临时 db，避免 LLM 硬编码
    路径导致连错库。若代码已经用 DATABASE_PATH / os.getenv，则不匹配、不误改。

    只改 AST 中的连接调用，保留原默认路径和连接参数，不修改字符串里的示例。
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    changed = False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "create_engine" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith("sqlite:///")):
            original = node.args[0]
            node.args[0] = ast.Call(
                func=ast.Attribute(value=ast.Name(id="os", ctx=ast.Load()), attr="getenv", ctx=ast.Load()),
                args=[ast.Constant(value="DATABASE_PATH"), original], keywords=[],
            )
            changed = True
    return ast.unparse(ast.fix_missing_locations(tree)) if changed else content


def _patch_django_datetime(content: str) -> str:
    """把 Django 写法的 Date.today() / DateTime.utcnow() 换成 datetime 模块的函数。

    这些是纯机械错误：SQLAlchemy 的 Date/DateTime 是列类型，不带 today/now 方法。
    与其让 fixer 花一整轮 LLM 请求去修一个字符串，不如直接改掉。
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    replacements = {("Date", "today"): "date", ("DateTime", "now"): "datetime",
                    ("DateTime", "utcnow"): "datetime"}
    from stages.env.static_check import _bound_names
    alias = "_awm_datetime"
    bound = _bound_names(tree)
    while alias in bound:
        alias += "_"
    changed = False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and (node.value.id, node.attr) in replacements):
            node.value = ast.Attribute(
                value=ast.Name(id=alias, ctx=ast.Load()),
                attr=replacements[(node.value.id, node.attr)], ctx=ast.Load(),
            )
            changed = True
    if not changed:
        return content
    # Column 的默认值需要每次插入时计算，而非导入模块时固定。
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Column":
            for kw in node.keywords:
                value = kw.value
                if (kw.arg in {"default", "onupdate"} and isinstance(value, ast.Call)
                        and not value.args and not value.keywords and isinstance(value.func, ast.Attribute)
                        and isinstance(value.func.value, ast.Attribute)
                        and isinstance(value.func.value.value, ast.Name)
                        and value.func.value.value.id == alias):
                    kw.value = value.func
    _insert_import(tree, f"import datetime as {alias}")
    return ast.unparse(ast.fix_missing_locations(tree))


def _patch_relative_imports(content: str, path: str = "", paths=None) -> str:
    """把相对 import（from .database import X / from ..models import Y）改成绝对 import。

    本项目的 modules（database.py / models.py / schemas.py）和 routers/ 都在顶层，
    相对 import 会报 "attempted relative import with no known parent package"。
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    from stages.env.static_check import _resolve_import_module
    changed = False
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom) and node.level
                and node.module in {"database", "models", "schemas"}):
            resolved = _resolve_import_module(path, node)
            if paths and resolved and (resolved.replace(".", "/") + ".py") in paths:
                continue
            node.level = 0
            changed = True
    return ast.unparse(tree) if changed else content


def _patch_missing_imports(content: str, path: str) -> str:
    """补上用到但没 import 的 sqlalchemy / datetime 名字。

    日志里最高频的崩溃是 models.py 写 Column(DateTime, default=datetime.utcnow) 却漏了
    from datetime import datetime。规则完全确定，直接补，不走 LLM。
    """
    from stages.env.static_check import (
        SQLALCHEMY_NAMES, DATETIME_NAMES, TYPING_NAMES, COMMON_NAMES, COMMON_IMPORTS,
        _bound_names, _used_names,
    )

    try:
        tree = ast.parse(strip_code_fences(content))
    except SyntaxError:
        return content

    used = _used_names(tree)
    bound = _bound_names(tree)
    missing_sa = sorted((used & SQLALCHEMY_NAMES) - bound)
    missing_dt = sorted((used & DATETIME_NAMES) - bound)
    missing_typing = sorted((used & TYPING_NAMES) - bound)
    missing_common = sorted((used & COMMON_NAMES) - bound)
    if not (missing_sa or missing_dt or missing_typing or missing_common):
        return content

    header = []
    if missing_sa:
        header.append(f"from sqlalchemy import {', '.join(missing_sa)}")
    if missing_dt:
        header.append(f"from datetime import {', '.join(missing_dt)}")
    if missing_typing:
        header.append(f"from typing import {', '.join(missing_typing)}")
    if missing_common:
        header.extend(COMMON_IMPORTS[name] for name in missing_common)
    insert_at = 0
    for node in tree.body:
        if (insert_at == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            # 跳过模块 docstring，import 必须放在它后面
            insert_at = node.end_lineno
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # 跟着已有 import 放在一起；遇到任何其它语句就停下，插到它前面
            insert_at = node.end_lineno
        else:
            break
    lines = content.split("\n")
    return "\n".join(lines[:insert_at] + header + lines[insert_at:])


def _patch_bad_column_kwargs(content: str) -> str:
    """把明确的字符串 check_constraint 转为 CheckConstraint，保留约束。"""
    try:
        tree = ast.parse(strip_code_fences(content))
    except SyntaxError:
        return content
    changed = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Column"):
            continue
        for kw in list(node.keywords):
            if kw.arg == "check_constraint" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                node.args.append(ast.Call(func=ast.Name(id="CheckConstraint", ctx=ast.Load()),
                                          args=[kw.value], keywords=[]))
                node.keywords.remove(kw)
                changed = True
    if not changed:
        return content
    try:
        return ast.unparse(ast.fix_missing_locations(tree))
    except Exception:  # noqa: BLE001
        return content


# 注意：routers/*.py 里引用顶层模块（database/models/schemas）时，必须用「绝对 import」
# （如 `from database import SessionLocal`），不要用相对 import（`from ..database import ...`）。
# 原因：database.py / models.py / schemas.py 是「顶层模块」（直接在项目根），routers 是「顶层包」；
# 相对 import 的 `..` 会试图跳出顶层包，报 "attempted relative import beyond top-level package"。


def _patch_model_config(content: str) -> str:
    """给缺少显式配置的 Pydantic XxxOut 响应模型补 from_attributes=True。

    FastAPI 用 response_model 把 SQLAlchemy ORM 对象转成 Pydantic 响应模型时，必须
    from_attributes=True，否则会报错。这里兜底自动补上（对纯 dict 构造的模型无副作用），
    避免 LLM 漏写导致反复重生成。解析失败或无需改动则原样返回。
    """
    try:
        tree = ast.parse(strip_code_fences(content))
    except SyntaxError:
        return content

    from stages.env.static_check import _bound_names

    base_names = set()
    module_names = set()
    config_expression = None
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pydantic":
            for alias in node.names:
                if alias.name == "BaseModel":
                    base_names.add(alias.asname or alias.name)
                elif alias.name == "ConfigDict":
                    config_expression = alias.asname or alias.name
        elif isinstance(node, ast.Import):
            module_names.update(alias.asname or alias.name for alias in node.names if alias.name == "pydantic")

    if config_expression is None and module_names:
        config_expression = f"{sorted(module_names)[0]}.ConfigDict"
    need_import = config_expression is None
    if need_import:
        config_expression = "ConfigDict"
        bound = _bound_names(tree)
        while config_expression in bound:
            config_expression = "_awm_" + config_expression

    # 同文件中的响应模型可能间接继承 BaseModel。
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    model_names = set(base_names)
    while True:
        previous = set(model_names)
        for node in classes:
            if any((isinstance(base, ast.Name) and base.id in model_names)
                   or (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                       and base.value.id in module_names and base.attr == "BaseModel")
                   for base in node.bases):
                model_names.add(node.name)
        if model_names == previous:
            break

    changed = False
    for node in classes:
        if node.name.endswith("Out") and node.name in model_names:
            has_config = any(
                (isinstance(stmt, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "model_config" for target in stmt.targets))
                or (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == "model_config")
                or (isinstance(stmt, ast.ClassDef) and stmt.name == "Config")
                for stmt in node.body
            )
            if not has_config:
                idx = 0
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    idx = 1  # 跳过 docstring
                node.body.insert(idx, ast.parse(f"model_config = {config_expression}(from_attributes=True)").body[0])
                changed = True

    if not changed:
        return content

    if need_import:
        statement = "from pydantic import ConfigDict"
        if config_expression != "ConfigDict":
            statement += f" as {config_expression}"
        _insert_import(tree, statement)

    return ast.unparse(tree)


def _ensure_package_inits(files: list[dict]) -> list[dict]:
    """给每个有 .py 子文件的目录（如 routers/）补一个空的 __init__.py。

    原因：main.py 用 `from routers import xxx` 导入 routers 包，这要求 routers 是「普通包」（有 __init__.py）。
    若 Architect 没规划 routers/__init__.py，`from routers import ...` 可能无法把 routers 正确识别为包。
    这里兜底：凡是目录下有 .py 子文件但缺 __init__.py 的，补一个空文件。
    """
    result = list(files)
    existing = {f["path"].strip().lstrip("/").lstrip("\\") for f in files}
    subdirs = set()
    for f in files:
        path = f["path"].strip().lstrip("/").lstrip("\\")
        parts = path.split("/")[:-1]
        for index in range(1, len(parts) + 1):
            subdirs.add("/".join(parts[:index]))
    for subdir in sorted(subdirs):
        init_path = f"{subdir}/__init__.py"
        if init_path not in existing:
            result.append({"path": init_path, "content": ""})
    return result


def prepare_files(files: list[dict], patch_db: bool = True) -> list[dict]:
    """统一规范化代码，检查、测试和最终落盘使用同一份结果。"""
    from stages.env.validators import validate_codegen_output

    # 格式或语法损坏的输出交给 fixer；补丁不能掩盖解析错误。
    if validate_codegen_output({"files": files}):
        return files
    result = []
    files = _ensure_package_inits(files)
    paths = {f["path"].strip() for f in files}
    for f in files:
        path = f["path"].strip()
        content = strip_code_fences(f["content"])
        if patch_db:
            content = _patch_engine(content)
            content = _patch_django_datetime(content)
            content = _patch_relative_imports(content, path, paths)
            content = _patch_bad_column_kwargs(content)
            content = _patch_missing_imports(content, path)
        if path.endswith("schemas.py"):
            content = _patch_model_config(content)
        result.append({"path": path, "content": content})
    return result


def write_files_to_dir(files: list[dict], base_dir: str, patch_db: bool = False) -> list[str]:
    """把 files（[{path, content}]）落盘到 base_dir，保持目录结构（自动建中间目录）。

    返回实际写入的文件绝对路径列表。
    """
    from stages.env.validators import validate_codegen_output

    files = prepare_files(files, patch_db=patch_db)
    problems = validate_codegen_output({"files": files})
    if problems:
        raise ValueError("代码包未通过校验：" + "; ".join(problems))
    written: list[str] = []
    for f in files:
        path = f["path"].strip().lstrip("/").lstrip("\\")
        content = strip_code_fences(f.get("content", ""))
        full = os.path.join(base_dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(full)
    return written


def _kill(proc: subprocess.Popen):
    """温和终止子进程，超时则强杀（跨平台，替代 Windows 没有的 os.killpg）。"""
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=1)


def _wait_for_http(port: int, timeout: float = 30.0, process=None) -> bool:
    """探测 FastAPI 的 /openapi.json 端点，判断服务器是否就绪。

    不依赖 MCP：env 生成的是普通 FastAPI（不挂载 MCP），原版 wait_for_server 探测 /mcp
    会一直 404 导致误判超时；这里改成探测 FastAPI 默认自带的 /openapi.json。
    """
    import httpx

    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=1, trust_env=False) as client:
        while time.monotonic() < deadline:
            check_cancelled()
            if process is not None and process.poll() is not None:
                return False
            try:
                r = client.get(f"{base}/openapi.json")
                if r.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            interruptible_sleep(0.2)
    return False


def _probe_endpoints(port: int, timeout: float = 15.0) -> list[str]:
    """请求 /openapi.json，对所有「无路径参数的 GET endpoint」逐个 GET，检测 5xx。

    这是针对高频 bug 设计的：response_model 把日期标成 str 而 ORM 返回 datetime
    → Pydantic 序列化 500。服务器能启动但接口一调就 500 的情况在这里被拦下。
    """
    import httpx

    errors: list[str] = []
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            spec_resp = client.get(f"{base}/openapi.json")
            if spec_resp.status_code != 200:
                return [f"GET /openapi.json -> {spec_resp.status_code}"]
            paths = spec_resp.json().get("paths", {}) or {}
            for path, methods in paths.items():
                check_cancelled()
                if "get" not in methods or "{" in path:
                    continue
                try:
                    r = client.get(f"{base}{path}")
                    if r.status_code >= 500:
                        errors.append(f"GET {path} -> {r.status_code}")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"GET {path} -> {type(e).__name__}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"probe 失败: {type(e).__name__}: {e}")
    return errors


def _extract_request_example(op: dict):
    """从 OpenAPI 的 requestBody 里提取一个可用的 example（用于 POST 冒烟测试）。"""
    rb = op.get("requestBody", {}) or {}
    content = (rb.get("content", {}) or {}).get("application/json", {}) or {}
    if "example" in content:
        return content["example"]
    schema = content.get("schema", {}) or {}
    if "example" in schema:
        return schema["example"]
    examples = content.get("examples", {}) or {}
    if examples:
        first = next(iter(examples.values()), {}) or {}
        if "value" in first:
            return first["value"]
    return None


def _probe_post_endpoints(port: int, timeout: float = 15.0) -> list[str]:
    """对 POST/PUT/PATCH endpoint 用 requestBody 的 example 发请求，检测 5xx。

    补足 _probe_endpoints 只测 GET 的盲区：POST 的 body 参数、response_model 序列化、
    多 Body 参数泄漏等 bug 只在写请求里暴露（如 rippling_payroll 的 create_bonus）。
    """
    import httpx

    base = f"http://127.0.0.1:{port}"
    errors = []
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(f"{base}/openapi.json")
            if response.status_code != 200:
                return [f"GET /openapi.json -> {response.status_code}"]
            spec = response.json()
            for path, methods in (spec.get("paths", {}) or {}).items():
                for method, op in methods.items():
                    check_cancelled()
                    if method.lower() not in ("post", "put", "patch"):
                        continue
                    if "{" in path:
                        continue
                    example = _extract_request_example(op)
                    if example is None:
                        continue
                    try:
                        r = client.request(method.upper(), f"{base}{path}", json=example)
                        if r.status_code >= 500:
                            errors.append(f"{method.upper()} {path} -> {r.status_code}")
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{method.upper()} {path} -> {type(e).__name__}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"POST probe 失败: {type(e).__name__}: {e}")
    return errors


_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\):.*?(?=\n\d{4}-\d{2}-\d{2}|\Z)",
    re.DOTALL,
)


def _extract_tracebacks(stdout: str) -> list[str]:
    """从服务器 stdout 提取所有 traceback 块（含出错文件路径 + 行号）。"""
    blocks = _TRACEBACK_RE.findall(stdout or "")
    return [b.strip() for b in blocks]


def _build_smoke_errors(smoke_endpoints: list[str], stdout: str) -> list[str]:
    """把 5xx 的 endpoint 名单 + stdout 里的 traceback 拼成错误列表，供 fixer 定位。"""
    if not smoke_endpoints:
        return []
    errors = list(smoke_endpoints)
    tracebacks = _extract_tracebacks(stdout)
    if tracebacks:
        errors.append("=== traceback ===")
        errors.extend(tracebacks)
    return errors


def _is_transient_probe_error(message: str) -> bool:
    """传输超时允许在全新服务器和数据库副本上复测；复测仍失败交给 fixer。"""
    return any(token in (message or "") for token in (
        "ReadTimeout", "ConnectTimeout", "TimeoutException", "timed out", "RemoteProtocolError",
    ))


def test_multifile_server(
    files: list[dict],
    db_path: str,
    scenario_name: str,
    idx: int,
    startup_timeout: float = 30.0,
    smoke_timeout: float = 15.0,
    smoke_retry: int = 1,
) -> tuple[bool, str, list[str]]:
    """多文件启动测试 + endpoint 冒烟测试。

    返回 (started, output, smoke_errors)：
      - started: 服务器是否启动成功；
      - output: 启动失败时的报错输出（成功时为空字符串）；
      - smoke_errors: 启动成功后的 endpoint 5xx 列表（空列表表示无 5xx）。
    """
    for attempt in range(smoke_retry + 1):
        result = _test_multifile_server_once(files, db_path, scenario_name, startup_timeout, smoke_timeout)
        started, _, errors = result
        # 有确定的 5xx 或 traceback 时保留失败；仅传输错误才重建环境复测。
        if not started or not errors or not all(_is_transient_probe_error(e) for e in errors):
            return result
        if attempt < smoke_retry:
            logger.warning(f"{scenario_name}: 冒烟测试传输超时，使用全新数据库副本复测一次")
    return result


def _test_multifile_server_once(files, db_path, scenario_name, startup_timeout, smoke_timeout):
    unique_id = uuid.uuid4().hex[:8]
    port = get_random_available_port()
    wait_port_free(port)
    temp_dir = tempfile.mkdtemp(prefix=f"env_test_{unique_id}")
    temp_db = os.path.join(temp_dir, "test_env.db")
    server_process = None
    logfile = None

    try:
        write_files_to_dir(files, temp_dir, patch_db=True)
        shutil.copyfile(db_path, temp_db)

        env = os.environ.copy()
        env["DATABASE_PATH"] = f"sqlite:///{temp_db.replace(chr(92), '/')}"
        env["PORT"] = str(port)
        env["HOST"] = "127.0.0.1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        logger.debug(f"{scenario_name} 多文件测试 on port {port}, temp_dir={temp_dir}")
        # 日志直接写临时文件，避免无人读取 PIPE 时服务被填满的管道阻塞。
        logfile = tempfile.TemporaryFile()
        server_process = managed_popen(
            [sys.executable, "main.py"],
            cwd=temp_dir,
            stdout=logfile,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        if not _wait_for_http(port, timeout=startup_timeout, process=server_process):
            exited = server_process.poll() is not None
            _kill(server_process)
            logfile.seek(0)
            stdout = logfile.read().decode("utf-8", errors="replace")
            reason = f"进程已退出（exit={server_process.returncode}）" if exited else f"启动超时（{startup_timeout:g}s）"
            logger.error(f"{scenario_name} {reason}：\n{stdout[:2000]}")
            return False, f"{reason}\n{stdout}", []

        # 情况 3：启动成功 → endpoint 冒烟测试（GET + POST）
        smoke_endpoints = _probe_endpoints(port, smoke_timeout)
        smoke_endpoints += _probe_post_endpoints(port, smoke_timeout)
        # kill 服务器，读 stdout（含 uvicorn 打出的 5xx traceback）
        _kill(server_process)
        logfile.seek(0)
        stdout = logfile.read().decode("utf-8", errors="replace")
        # 把 endpoint 名单 + traceback 拼成有定位信息的错误
        smoke_errors = _build_smoke_errors(smoke_endpoints, stdout or "")
        return True, "", smoke_errors

    finally:
        if server_process is not None:
            try:
                if server_process.poll() is None:
                    _kill(server_process)
            finally:
                if server_process.poll() is not None:
                    release_process(server_process)
                if server_process.stdout is not None:
                    server_process.stdout.close()
        if logfile is not None:
            logfile.close()
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
