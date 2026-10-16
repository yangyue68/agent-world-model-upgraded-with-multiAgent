"""spec 阶段（API 规范）的规则校验（确定性、非 LLM）。"""

import re
from collections import Counter

from stages.spec.schema import validate_schema

# 认证类关键词：endpoint 的 path/operation_id 命中则说明混入了认证逻辑，应剔除
AUTH_KEYWORDS = ["login", "log_in", "signin", "sign_in", "logout", "log_out", "signout", "sign_out", "register", "sign_up", "signup", "token", "password"]

VALID_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
VALID_PARAM_TYPES = {"query", "path", "body"}


def rule_has_endpoints(group: dict):
    """规则：每个 group 必须有 endpoints。"""
    if not group.get("endpoints"):
        return "无 endpoints"
    return None


def rule_endpoint_fields(group: dict):
    """规则：每个 endpoint 必须有 path/method/operation_id，且 method 合法。"""
    for ep in group.get("endpoints", []):
        op = ep.get("operation_id") or "?"
        if not (ep.get("path") or "").startswith("/"):
            return f"endpoint「{op}」缺 path 或 path 不以 / 开头"
        if (ep.get("method") or "").upper() not in VALID_METHODS:
            return f"endpoint「{op}」method 非法"
        if not isinstance(ep.get("operation_id"), str) or not re.fullmatch(r"[a-z][a-z0-9_]*", ep["operation_id"]):
            return "endpoint 的 operation_id 必须是小写 snake_case"
    return None


def rule_valid_params(group: dict):
    """规则：每个参数的 param_type 必须合法。"""
    problems = []
    for ep in group.get("endpoints", []):
        op = ep.get("operation_id") or "?"
        params = ep.get("request_params")
        if isinstance(params, dict):
            items = params.items()
        elif isinstance(params, list):
            if any(not isinstance(p, dict) for p in params):
                return f"endpoint「{op}」参数列表元素必须是对象"
            names = [p.get("name") for p in params]
            if any(not isinstance(name, str) or not name.strip() for name in names):
                problems.append(f"endpoint「{op}」参数列表每项必须有非空 name")
            elif len(names) != len(set(names)):
                problems.append(f"endpoint「{op}」参数 name 重复")
            items = ((p.get("name", "?"), p) for p in params)
        else:
            return f"endpoint「{op}」request_params 必须是对象或列表"
        for name, p in items:
            if not isinstance(p, dict):
                return f"endpoint「{op}」参数 {name} 结构非法"
            if p.get("param_type") not in VALID_PARAM_TYPES:
                problems.append(f"endpoint「{op}」参数 {name} 的 param_type 非法（必须为 query/path/body）")
            if type(p.get("required")) is not bool:
                problems.append(f"endpoint「{op}」参数 {name} 的 required 必须是布尔值，object 必填字段放在 required_fields")
            errors = validate_schema(p, f"{op}.request_params.{name}", parameter=True)
            problems.extend(errors)
    return "; ".join(problems) if problems else None


def rule_path_params(group: dict):
    """路径占位符与必填 path 参数必须一一对应，不能交给 env 猜测。"""
    problems = []
    for ep in group.get("endpoints", []):
        op = ep.get("operation_id") or "?"
        params = ep.get("request_params")
        if isinstance(params, dict):
            items = params.items()
        elif isinstance(params, list):
            items = ((p.get("name"), p) for p in params if isinstance(p, dict))
        else:
            continue  # 参数结构错误由 rule_valid_params 报告。
        placeholders = set(re.findall(r"\{([^{}]+)\}", ep["path"]))
        path_names = set()
        for name, param in items:
            if not isinstance(param, dict) or param.get("param_type") != "path":
                continue
            if not isinstance(name, str):
                continue
            path_names.add(name)
            if param.get("required") is not True:
                problems.append(f"endpoint「{op}」路径参数 {name} 必须 required=true")
            kinds = param.get("type")
            if kinds == "null" or isinstance(kinds, list) and "null" in kinds:
                problems.append(f"endpoint「{op}」路径参数 {name} 不能为 null")
        for name in sorted(placeholders - path_names):
            problems.append(f"endpoint「{op}」URL 占位符 {{{name}}} 缺少同名必填 path 参数")
        for name in sorted(path_names - placeholders):
            problems.append(f"endpoint「{op}」path 参数 {name} 在 URL 中没有对应占位符；请改正 URL 或参数位置")
    return "; ".join(problems) if problems else None


def rule_endpoint_metadata(group: dict):
    """接口元数据完整，避免 env 生成器猜摘要、标签或请求参数。"""
    for ep in group.get("endpoints", []):
        op = ep.get("operation_id") or "?"
        if not isinstance(ep.get("summary"), str) or not ep["summary"].strip():
            return f"endpoint「{op}」缺 summary"
        if not isinstance(ep.get("description"), str) or not ep["description"].strip():
            return f"endpoint「{op}」缺完整 description"
        if not isinstance(ep.get("tags"), list) or not ep["tags"] or not all(isinstance(tag, str) and tag.strip() for tag in ep["tags"]):
            return f"endpoint「{op}」tags 必须是非空字符串列表"
        if "request_params" not in ep:
            return f"endpoint「{op}」缺 request_params"
        if "response" not in ep:
            return f"endpoint「{op}」缺 response"
    return None


def rule_response_contract(group: dict):
    """递归类型定义、示例用途和用户范围必须明确，避免 env 猜测。"""
    problems = []
    for ep in group.get("endpoints", []):
        op = ep.get("operation_id") or "?"
        problems.extend(validate_schema(ep.get("response"), f"{op}.response"))
        if ep.get("example_policy") != "illustrative":
            problems.append(f"{op}: example_policy 必须是 illustrative，示例不是预期查询结果")
        if ep.get("user_scope") not in ("current_user", "all"):
            problems.append(f"{op}: user_scope 必须是 current_user 或 all")
    return "; ".join(problems) if problems else None


def rule_no_auth(group: dict):
    """规则：endpoint 不能是认证类（login/register/token 等）。"""
    for ep in group.get("endpoints", []):
        text = f"{ep.get('path', '')} {ep.get('operation_id', '')}".lower()
        for kw in AUTH_KEYWORDS:
            if kw in text:
                return f"含认证端点「{ep.get('operation_id', '?')}」"
    return None


# spec 阶段的规则清单
SPEC_RULES = [rule_has_endpoints, rule_endpoint_fields, rule_endpoint_metadata,
              rule_valid_params, rule_path_params, rule_response_contract, rule_no_auth]


def validate_spec(api_groups: list, rules=None):
    """对每个 group 依次应用规则，返回 (保留的 group, 被剔除的 [{group, reasons}])。"""
    if rules is None:
        rules = SPEC_RULES
    kept = []
    dropped = []
    if not isinstance(api_groups, list) or not api_groups:
        return [], [{"group": "unknown", "reasons": ["api_groups 必须是非空列表"]}]
    names = Counter(g.get("group_name") for g in api_groups
                    if isinstance(g, dict) and isinstance(g.get("group_name"), str))
    operations = Counter()
    routes = Counter()
    for group in api_groups:
        endpoints = group.get("endpoints") if isinstance(group, dict) else None
        if not isinstance(endpoints, list):
            continue
        for ep in endpoints:
            if isinstance(ep, dict):
                if isinstance(ep.get("operation_id"), str):
                    operations[ep["operation_id"]] += 1
                if isinstance(ep.get("path"), str) and isinstance(ep.get("method"), str):
                    # /orders/{id} 和 /orders/{order_id} 是同一条路由。
                    route_path = re.sub(r"\{[^{}]+\}", "{}", ep["path"])
                    routes[(ep["method"].upper(), route_path)] += 1
    for group in api_groups:
        reasons = []
        if not isinstance(group, dict):
            dropped.append({"group": "unknown", "reasons": ["group 必须是对象"]})
            continue
        name = group.get("group_name")
        if not isinstance(name, str) or not name.strip():
            reasons.append("缺少有效 group_name")
        elif names[name] > 1:
            reasons.append("group_name 重复")
        endpoints = group.get("endpoints")
        valid_shape = isinstance(endpoints, list) and all(
            isinstance(ep, dict) and isinstance(ep.get("path"), str)
            and isinstance(ep.get("method"), str) for ep in endpoints)
        if not valid_shape:
            reasons.append("endpoints 必须是接口对象列表，path/method 必须是字符串")
        else:
            for rule in rules:
                problem = rule(group)
                if problem:
                    reasons.append(problem)
            for ep in endpoints:
                op = ep.get("operation_id")
                if isinstance(op, str) and operations[op] > 1:
                    reasons.append(f"operation_id 重复：{op}")
                route = (ep["method"].upper(), re.sub(r"\{[^{}]+\}", "{}", ep["path"]))
                if routes[route] > 1:
                    reasons.append(f"method/path 重复：{route}")
        if reasons:
            dropped.append({"group": group.get("group_name", "unknown"), "reasons": reasons})
        else:
            kept.append(group)
    return kept, dropped
