"""env 阶段（多文件环境代码）的规则校验（确定性、非 LLM）。

校验两类输出：
  - Architect 输出：{files: [{path, purpose}]}，必须含 main.py；
  - CodeGenerator / Fixer 输出：{files: [{path, content}]}（Fixer 可豁免 main.py）。

这些检查负责格式、语法和部分结构一致性；业务正确性仍需实际运行与轨迹验证。
"""

import ast
import re
from pathlib import PurePosixPath


def _strip_code_fences(code: str) -> str:
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


def _check_path(path, problems: list[str]):
    """校验单个文件路径：必须是非空相对路径（用 / 分隔，禁止绝对路径/反斜杠）。"""
    if not isinstance(path, str) or not path.strip():
        problems.append("文件缺 path")
        return
    p = path.strip()
    if (p.startswith("/") or p.startswith("\\") or p.startswith(".") or "\\" in p
            or ":" in p or ".." in PurePosixPath(p).parts):
        problems.append(f"非法路径: {p}（用相对路径，如 routers/users.py，禁止绝对路径/反斜杠）")
    if not p.endswith(".py"):
        problems.append(f"非 Python 文件: {p}")


# 注：不再用「缺 model_config」作为校验失败项——from_attributes 由 runner.py 的
# _patch_model_config 在落盘时自动补上（从 ORM 对象转 Pydantic 响应模型必须 from_attributes=True，
# 自动补比报错重生成更省 token、更稳定）。


def validate_architect_output(data) -> list[str]:
    """校验 Architect 输出：{files: [{path, purpose}]}，且必须含 main.py。

    返回问题列表（空列表表示通过）。
    """
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["输出不是 JSON 对象"]
    files = data.get("files")
    if not isinstance(files, list) or not files:
        return ["缺 files 字段，或 files 为空"]
    paths: list[str] = []
    for f in files:
        if not isinstance(f, dict):
            problems.append("files 元素不是对象")
            continue
        path = f.get("path", "")
        _check_path(path, problems)
        if isinstance(path, str) and path.strip():
            paths.append(path.strip())
    if "main.py" not in paths:
        problems.append("必须包含 main.py（服务器从 main.py 启动）")
    if "database.py" not in paths:
        problems.append("必须包含 database.py（训练运行时需要从此模块导入 Base）")
    if len(paths) != len(set(paths)):
        problems.append("文件路径重复")
    return problems


def validate_codegen_output(data, require_main: bool = True, check_syntax: bool = True) -> list[str]:
    """校验 CodeGenerator / Fixer 输出：{files: [{path, content}]}。

    require_main=False 用于 Fixer：它只返回修改过的文件，可以不含 main.py。
    每个文件的 content 会先去掉代码围栏再 ast.parse（语法正确才算通过）。
    """
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["输出不是 JSON 对象"]
    files = data.get("files")
    if not isinstance(files, list) or not files:
        return ["缺 files 字段，或 files 为空"]
    paths: list[str] = []
    for f in files:
        if not isinstance(f, dict):
            problems.append("files 元素不是对象")
            continue
        path = f.get("path", "")
        _check_path(path, problems)
        if isinstance(path, str) and path.strip():
            paths.append(path.strip())
        content = f.get("content", "")
        is_init = isinstance(path, str) and path.strip().endswith("__init__.py")
        if not isinstance(content, str):
            problems.append(f"{path}: content 必须是字符串")
        elif is_init:
            # __init__.py 是 Python 包标记文件，允许为空（空文件合法）；非空则仍需语法正确
            stripped = _strip_code_fences(content).strip()
            if stripped and check_syntax:
                try:
                    ast.parse(stripped)
                except SyntaxError as e:
                    problems.append(f"{path}: 语法错误 {e}")
        elif len(_strip_code_fences(content).strip()) < 10:
            problems.append(f"{path}: content 为空或过短")
        elif check_syntax:
            try:
                ast.parse(_strip_code_fences(content))
            except SyntaxError as e:
                problems.append(f"{path}: 语法错误 {e}")
    if len(paths) != len(set(paths)):
        problems.append("文件路径重复")
    if require_main and "main.py" not in paths:
        problems.append("必须包含 main.py")
    return problems


def _split_top_level(s: str) -> list[str]:
    """按「顶层逗号」分割（忽略嵌套括号内的逗号），用于正确切分 CREATE TABLE 的列/约束。"""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _parse_ddl_columns(ddl: str) -> list[str]:
    """从 CREATE TABLE 语句提取列名。

    正确处理嵌套括号（CHECK(...) 里的枚举值、FOREIGN KEY ... REFERENCES 里的列）——
    用括号计数定位 CREATE TABLE 的完整括号体，再按「顶层逗号」切分，
    跳过 PRIMARY/FOREIGN/CHECK/UNIQUE/CONSTRAINT 开头的约束行。
    """
    m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\w.]+", ddl, re.IGNORECASE)
    if not m:
        return []
    start = ddl.find("(", m.end())
    if start == -1:
        return []
    depth = 0
    end = -1
    for i in range(start, len(ddl)):
        if ddl[i] == "(":
            depth += 1
        elif ddl[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return []
    body = ddl[start + 1:end]
    cols: list[str] = []
    for part in _split_top_level(body):
        part = part.strip()
        if not part:
            continue
        upper = part.upper()
        if upper.startswith(("PRIMARY", "FOREIGN", "CHECK", "UNIQUE", "CONSTRAINT")):
            continue
        cols.append(part.split()[0].strip("`\""))
    return cols


def _parse_model_columns(content: str) -> dict[str, list[str]]:
    """从 models.py 源码提取 {__tablename__: [列名, ...]}（用 AST）。"""
    try:
        tree = ast.parse(_strip_code_fences(content))
    except SyntaxError:
        return {}
    models: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        tablename = None
        cols: list[str] = []
        for stmt in node.body:
            # __tablename__ = "xxx"
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == "__tablename__"
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                tablename = stmt.value.value
            # attr = Column(...) 或 attr = Column("real_name", ...)
            elif (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id == "Column"
            ):
                call = stmt.value
                if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    cols.append(call.args[0].value)  # Column("real_name", ...) 的实际列名
                else:
                    cols.append(stmt.targets[0].id)  # attr = Column(...) 的列名 = 属性名
        if tablename:
            models[tablename] = cols
    return models


def validate_model_columns(files: list[dict], db_schema: dict | None) -> list[str]:
    """校验 env 代码 model 的列名是否与 db schema 完全一致（不重命名、不发明、不遗漏）。

    返回问题列表（空列表表示通过）。db_schema 为空或 models.py 缺失时跳过（返回空）。
    """
    problems: list[str] = []
    if not db_schema:
        return problems

    # 1. 从 db_schema 提取每表列名集合
    db_columns: dict[str, set[str]] = {}
    for t in db_schema.get("tables", []) or []:
        name = t.get("name", "")
        ddl = t.get("ddl", "")
        if name:
            db_columns[name] = set(_parse_ddl_columns(ddl))

    # 2. 从 files 里 models.py 提取 model 列名
    models_content = ""
    for f in files:
        if isinstance(f, dict) and f.get("path", "").endswith("models.py"):
            models_content = f.get("content", "")
            break
    if not isinstance(models_content, str) or not models_content.strip():
        return problems

    model_columns = _parse_model_columns(models_content)

    # 3. 比对：model 多了列 / 漏了列 / 表不存在
    for tablename, model_cols in model_columns.items():
        if tablename not in db_columns:
            problems.append(f"表 '{tablename}' 在 db schema 中不存在（model 发明了不存在的表）")
            continue
        db_cols = db_columns[tablename]
        model_set = set(model_cols)
        extra = sorted(model_set - db_cols)
        missing = sorted(db_cols - model_set)
        if extra:
            problems.append(f"表 '{tablename}' 的 model 多了 db 没有的列：{extra}")
        if missing:
            problems.append(f"表 '{tablename}' 的 model 漏了 db 有的列：{missing}")
    return problems
