"""结构校验与无歧义归一化；自然语言业务和数据库引用由 Reviewer 检查。"""

import re
from collections import Counter
from copy import deepcopy

from .schema import validate_schema

# 认证类关键词：endpoint 的 path/operation_id 命中则说明混入了认证逻辑，应剔除
AUTH_KEYWORDS = ["login", "log_in", "signin", "sign_in", "logout", "log_out", 
                  "signout", "sign_out", "register", "sign_up", "signup", "token", "password"]


def normalize_groups(groups):
    """不补造业务字段。仅统一 method 和可省略的展示元数据。"""
    result = deepcopy(groups)
    if not isinstance(result, list):
        return result
    for group in result:
        if not isinstance(group, dict) or not isinstance(group.get("endpoints"), list):
            continue
        for ep in group["endpoints"]:
            if not isinstance(ep, dict):
                continue
            if isinstance(ep.get("method"), str):
                ep["method"] = ep["method"].upper()
            ep.setdefault("tags", [group.get("group_name", "")])
            ep.setdefault("example_policy", "illustrative")
    return result


def validate_spec(groups):
    """返回 {group_name: [具体错误]}；不静默丢弃接口。"""
    if not isinstance(groups, list) or not groups:
        return {"unknown": ["api_groups 必须是非空数组"]}
    problems = {}
    names, operations, routes = Counter(), Counter(), Counter()
    for group in groups:
        if not isinstance(group, dict):
            continue
        if isinstance(group.get("group_name"), str):
            names[group["group_name"]] += 1
        if not isinstance(group.get("endpoints"), list):
            continue
        for ep in group["endpoints"]:
            if not isinstance(ep, dict):
                continue
            if isinstance(ep.get("operation_id"), str):
                operations[ep["operation_id"]] += 1
            if isinstance(ep.get("path"), str) and isinstance(ep.get("method"), str):
                routes[(ep["method"].upper(), re.sub(r"\{[^{}]+\}", "{}", ep["path"]))] += 1
    for i, group in enumerate(groups):
        name = group.get("group_name") if isinstance(group, dict) else None
        key = name if isinstance(name, str) and name else f"group_{i}"
        errors = []
        if not isinstance(name, str) or not name.strip():
            errors.append("缺少非空 group_name")
        elif names[name] > 1:
            errors.append("group_name 重复")
        endpoints = group.get("endpoints") if isinstance(group, dict) else None
        if not isinstance(endpoints, list) or not endpoints:
            errors.append("endpoints 必须是非空数组")
        else:
            for j, ep in enumerate(endpoints):
                if not isinstance(ep, dict):
                    errors.append(f"endpoint[{j}] 必须是对象")
                    continue
                op = ep.get("operation_id")
                prefix = op if isinstance(op, str) else f"endpoint[{j}]"
                if not isinstance(op, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", op):
                    errors.append(f"{prefix}: operation_id 必须为小写 snake_case")
                elif operations[op] > 1:
                    errors.append(f"{prefix}: operation_id 重复")
                path, method = ep.get("path"), ep.get("method")
                valid_path = isinstance(path, str) and path.startswith("/")
                if not valid_path:
                    errors.append(f"{prefix}: path 必须以 / 开头")
                if not isinstance(method, str) or method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    errors.append(f"{prefix}: HTTP method 不合法")
                elif valid_path and routes[(method.upper(), re.sub(r"\{[^{}]+\}", "{}", path))] > 1:
                    errors.append(f"{prefix}: method/path 重复")
                for field in ("summary", "description"):
                    if not isinstance(ep.get(field), str) or not ep[field].strip():
                        errors.append(f"{prefix}: 缺少 {field}")
                # 认证类端点拦截
                auth_text = f"{path or ''} {op or ''}".lower()
                for kw in AUTH_KEYWORDS:
                    if kw in auth_text:
                        errors.append(f"{prefix}: 含认证关键词「{kw}」，任务不需要认证类端点")
                        break
                if ep.get("user_scope") not in ("all", "current_user"):
                    errors.append(f"{prefix}: user_scope 必须为 all/current_user")
                if ep.get("example_policy", "illustrative") != "illustrative":
                    errors.append(f"{prefix}: example_policy 只能为 illustrative")
                errors.extend(validate_schema(ep.get("response"), f"{prefix}.response"))
                params = ep.get("request_params")
                if not isinstance(params, dict):
                    errors.append(f"{prefix}: request_params 必须是名称到参数的对象")
                    continue
                placeholders = set(re.findall(r"\{([^{}]+)\}", path)) if valid_path else set()
                path_names = set()
                for param_name, param in params.items():
                    label = f"{prefix}.request_params.{param_name}"
                    if not isinstance(param_name, str) or not param_name.strip() or not isinstance(param, dict):
                        errors.append(f"{label}: 参数必须具有非空名称和对象定义")
                        continue
                    if param.get("param_type") not in ("path", "query", "body"):
                        errors.append(f"{label}: param_type 必须为 path/query/body")
                    if type(param.get("required")) is not bool:
                        errors.append(f"{label}: required 必须为 boolean")
                    errors.extend(validate_schema(param, label, parameter=True))
                    if param.get("param_type") == "path":
                        path_names.add(param_name)
                        kinds = param.get("type")
                        if param.get("required") is not True or kinds == "null" or isinstance(kinds, list) and "null" in kinds:
                            errors.append(f"{label}: path 参数必须必填且非 null")
                if placeholders != path_names:
                    errors.append(f"{prefix}: URL 占位符与 path 参数不一致：{sorted(placeholders)} vs {sorted(path_names)}")
        if errors:
            problems.setdefault(key, []).extend(errors)
    return problems


def parse_plan(data):
    groups = data.get("groups") if isinstance(data, dict) else data
    if not isinstance(groups, list) or not groups or any(not isinstance(g, str) or not g.strip() for g in groups):
        raise ValueError('Planner 需要 {"groups": ["Resource"]}，不能为空')
    return list(dict.fromkeys(g.strip() for g in groups))


def parse_group(data, name, *, allow_empty=False):
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(endpoints, list) or any(not isinstance(ep, dict) for ep in endpoints):
        raise ValueError("生成/修复结果必须包含 endpoints 对象数组")
    if not endpoints and not allow_empty:
        raise ValueError("不接受空 endpoints；必须返回完整替换分组")
    return {"group_name": name, "endpoints": endpoints}


def parse_review(data, groups):
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ValueError("Reviewer 必须返回 results 数组")
    expected = {g["group_name"] for g in groups}
    found = set()
    for row in results:
        if not isinstance(row, dict) or not isinstance(row.get("group"), str):
            raise ValueError("评审条目缺少 group")
        name = row["group"]
        if name not in expected or name in found:
            raise ValueError(f"评审包含未知或重复分组：{name}")
        found.add(name)
        if type(row.get("pass")) is not bool or not isinstance(row.get("issue"), str):
            raise ValueError(f"{name}: pass 必须为布尔，issue 必须为字符串")
        if not row["pass"] and not row["issue"].strip():
            raise ValueError(f"{name}: 未通过必须给出具体 issue")
    if found != expected:
        raise ValueError(f"评审遗漏分组：{sorted(expected - found)}")
    return results
