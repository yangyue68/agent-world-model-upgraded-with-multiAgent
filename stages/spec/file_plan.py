"""按 API 分组确定环境文件结构，不调用 LLM，也不改写接口规范。"""

from copy import deepcopy

from stages.spec.validators import validate_spec


# env 的公共模块契约。router 只能导入公共模块，不能互相导入。
ENV_MODULE_CONTRACT = {
    "database.py": {
        "exports": ["Base", "engine", "SessionLocal", "get_db"],
        "database_path_env": "DATABASE_PATH",
    },
    "models.py": {"imports": ["database.Base"], "source": "db_schema"},
    "schemas.py": {"source": "api_spec"},
    "router": {
        "exports": ["router"],
        "allowed_project_imports": ["database", "models", "schemas"],
        "session_dependency": "database.get_db",
        "cross_router_imports": False,
    },
    "main.py": {"exports": ["app"], "register_all_routers": True},
}


def build_files(api_spec: dict) -> list[dict]:
    """固定骨架 + 每个分组一个 router；顺序沿用已校验的 api_groups。"""
    files = [
        {"path": "main.py", "kind": "app"},
        {"path": "database.py", "kind": "database"},
        {"path": "models.py", "kind": "orm"},
        {"path": "schemas.py", "kind": "schemas"},
        {"path": "routers/__init__.py", "kind": "package"},
    ]
    for index, group in enumerate(api_spec["api_groups"], start=1):
        files.append({
            "path": f"routers/group_{index:03d}.py",
            "kind": "router",
            "group_name": group["group_name"],
            "operation_ids": [ep["operation_id"] for ep in group["endpoints"]],
        })
    return files


def validate_files(api_spec: dict, files: list) -> list[str]:
    """验证固定结构和接口归属，拒绝缺失、重复、额外或顺序错误的映射。"""
    if not isinstance(api_spec, dict):
        return ["api_spec 必须是对象"]
    _, dropped = validate_spec(api_spec.get("api_groups"))
    if dropped:
        return ["api_spec 未通过接口规则检查"]
    try:
        expected = build_files(api_spec)
    except (KeyError, TypeError):
        return ["api_spec 结构非法，无法生成 files"]
    if not isinstance(files, list):
        return ["files 必须是列表"]
    if files != expected:
        return ["files 与固定结构或 API 分组不一致；请根据最终 api_spec 重新生成"]
    operations = [op for file in files if file["kind"] == "router"
                  for op in file["operation_ids"]]
    if len(operations) != len(set(operations)):
        return ["operation_id 重复，不能唯一确定接口归属"]
    return []


def build_spec_result(scenario: str, task_id: str, task, api_groups: list) -> dict:
    """从已通过规则和语义检查的规范生成任务级输出，保留全部接口内容。"""
    api_spec = {"api_groups": deepcopy(api_groups)}
    files = build_files(api_spec)
    problems = validate_files(api_spec, files)
    if problems:
        raise ValueError("; ".join(problems))
    return {
        "scenario": scenario, "task_id": task_id, "task": task,
        "api_spec": api_spec, "files": files,
        "module_contract": deepcopy(ENV_MODULE_CONTRACT),
    }
