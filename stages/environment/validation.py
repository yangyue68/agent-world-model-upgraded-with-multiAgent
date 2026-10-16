"""Deterministic source, OpenAPI, database and trajectory checks."""
import ast
import json
import re
import sqlalchemy
from lib.db_diff import diff_snapshots, snapshot_db


def normalize_code(code, api_spec=None):
    text = str(code).replace("\r\n", "\n")
    text = re.sub(
        r"(?m)^(\s*DATABASE_PATH\s*=\s*os\.(?:environ\.get|getenv)\(\s*[\"'])DATABASE_URL([\"'][^\n]*\))",
        r"\1DATABASE_PATH\2",
        text,
    )
    text = re.sub(
        r"(?m)^\s*DATABASE_PATH\s*=\s*os\.(?:environ\.get|getenv)\(\s*[\"']DATABASE_PATH[\"']\s*,[^\n]*\)\s*$",
        'DATABASE_PATH = os.environ.get("DATABASE_PATH")',
        text,
    )
    text = re.sub(
        r"create_engine\(\s*[fF]([\"'])sqlite:///\{DATABASE_PATH\}\1\s*\)",
        "create_engine(DATABASE_PATH)",
        text,
    )
    if re.search(r"sqlite:///", text) and not re.search(r"DATABASE_PATH\s*=\s*os\.(?:environ|getenv)", text):
        if not re.search(r"^\s*import\s+os\b", text, re.M):
            text = "import os\n" + text
        text = re.sub(r"(?m)^\s*DATABASE_PATH\s*=\s*['\"]sqlite:///[^'\"]*['\"]", 'DATABASE_PATH = os.environ.get("DATABASE_PATH")', text)
        text = re.sub(r"create_engine\(\s*['\"]sqlite:///[^'\"]*['\"]\s*\)", "create_engine(DATABASE_PATH)", text)
    if isinstance(api_spec, dict):
        expected = {(str(e.get("method", "")).lower(), e.get("path")): e.get("operation_id")
                    for g in api_spec.get("api_groups", []) for e in g.get("endpoints", [])
                    if e.get("path") and e.get("operation_id")}
        pattern = re.compile(r'(@app\.(?P<method>get|post|put|patch|delete|head|options)\(\s*["\'](?P<path>[^"\']+)["\'][^)]*?operation_id\s*=\s*["\'])(?P<name>[^"\']+)(["\'])', re.I | re.S)
        def replace(match):
            wanted = expected.get((match.group("method").lower(), match.group("path")))
            return match.group(1) + wanted + match.group(5) if wanted else match.group(0)
        text = pattern.sub(replace, text)
    return text.strip() + "\n"


def check_source(code):
    try:
        tree = ast.parse(code, filename="main.py")
        compile(tree, "main.py", "exec")
    except (SyntaxError, ValueError) as exc:
        return [f"Python syntax error: {exc}"]
    problems = []
    if not re.search(r"os\.environ\s*(?:\[\s*|\.get\(\s*)[\"']DATABASE_PATH|os\.getenv\(\s*[\"']DATABASE_PATH", code):
        problems.append("DATABASE_PATH must be read from the runtime environment")
    if re.search(r"(?:create_engine\s*\(|DATABASE_PATH\s*=\s*)\s*[\"']sqlite:///", code):
        problems.append("hardcoded sqlite database URL detected")
    if re.search(r"\b(?:create_all|drop_all)\s*\(", code):
        problems.append("generated code must not recreate or drop the upstream schema")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and (node.module.startswith("main") or node.module.startswith("routers")):
            problems.append(f"generated code imports unavailable module {node.module}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"commit", "flush", "add", "delete", "execute"}:
            parent_text = ast.get_source_segment(code, node) or ""
            if "select" not in parent_text.lower() and node.func.attr in {"commit", "flush", "add", "delete"}:
                pass
    if not re.search(r"\bapp\s*=\s*FastAPI\s*\(", code):
        problems.append("main.py must export FastAPI app")
    return list(dict.fromkeys(problems))


def check_openapi(api_spec, document):
    problems = []
    paths = document.get("paths", {}) if isinstance(document, dict) else {}
    for group in api_spec.get("api_groups", []):
        for endpoint in group.get("endpoints", []):
            method, path, operation = str(endpoint.get("method", "")).lower(), endpoint.get("path"), endpoint.get("operation_id")
            item = paths.get(path, {})
            operation_doc = item.get(method)
            if not operation_doc:
                problems.append(f"missing route {method.upper()} {path}")
                continue
            if operation_doc.get("operationId") != operation:
                problems.append(f"{path}: operationId must be {operation}")
            required = endpoint.get("request_params", {})
            declared = {p.get("name"): p for p in operation_doc.get("parameters", [])}
            body_required = {}
            for name, detail in required.items():
                if not detail.get("required"):
                    continue
                if detail.get("param_type") == "body":
                    body_required[name] = detail
                elif name not in declared or not declared[name].get("required"):
                    problems.append(f"{operation}: required parameter {name} missing")
            if body_required:
                request_body = _resolve_openapi_ref(operation_doc.get("requestBody", {}), document)
                content = request_body.get("content", {}) if isinstance(request_body, dict) else {}
                json_content = content.get("application/json") or next(
                    (value for media_type, value in content.items() if media_type.endswith("+json")), {}
                )
                schema = json_content.get("schema", {}) if isinstance(json_content, dict) else {}
                properties, body_declared_required = _body_schema_fields(schema, document)
                for name in body_required:
                    if name not in properties or name not in body_declared_required:
                        problems.append(f"{operation}: required body field {name} missing")
                for name, detail in required.items():
                    if name in properties:
                        problems.extend(_check_request_schema(detail, properties[name], f"{operation}.{name}", document))
    return problems


def repair_name_error_import(code, problems):
    missing = set()
    for problem in problems:
        missing.update(re.findall(r"NameError: name ['\"]([A-Za-z_]\w*)['\"] is not defined", str(problem)))
    if not missing:
        return code
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy"
        for alias in node.names
    }
    symbols = sorted(name for name in missing if name not in imported and hasattr(sqlalchemy, name))
    if not symbols:
        return code
    lines = code.splitlines()
    insert_at = 0
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, (ast.Constant, ast.Str)):
        insert_at = body[0].end_lineno
    for node in body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            insert_at = max(insert_at, node.end_lineno)
    lines[insert_at:insert_at] = [f"from sqlalchemy import {name}" for name in symbols]
    return "\n".join(lines) + ("\n" if code.endswith("\n") else "")


def summarize_problems(problems):
    summaries = []
    seen = set()
    for problem in problems:
        text = str(problem)
        if "Traceback (most recent call last):" in text:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            exception_lines = [
                line for line in lines
                if re.match(r"(?:[\w.]+(?:Error|Exception)|AssertionError):", line)
            ]
            frames = [line for line in lines if re.search(r"File .*main\.py\", line \d+, in ", line)]
            detail = exception_lines[-1] if exception_lines else lines[-1]
            if frames:
                detail = f"{frames[-1]}: {detail}"
        else:
            detail = text.strip()
        if detail and detail not in seen:
            seen.add(detail)
            summaries.append(detail)
    return summaries


def generated_code_runtime_errors(problems):
    summaries = summarize_problems(problems)
    return [
        problem for problem in summaries
        if re.search(r"File ['\"].*main\.py['\"], line \d+, in ", problem)
    ]


def _resolve_openapi_ref(schema, document, seen=()):
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if not ref or ref in seen or not ref.startswith("#/"):
        return schema
    target = document
    try:
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
    except (KeyError, TypeError):
        return schema
    merged = dict(target) if isinstance(target, dict) else {}
    merged.update({key: value for key, value in schema.items() if key != "$ref"})
    return _resolve_openapi_ref(merged, document, seen + (ref,))


def _body_schema_fields(schema, document):
    properties, required = {}, set()
    pending = [schema]
    while pending:
        current = _resolve_openapi_ref(pending.pop(), document)
        if not isinstance(current, dict):
            continue
        properties.update(current.get("properties", {}))
        required.update(current.get("required", []))
        pending.extend(current.get("allOf", []))
    return properties, required


def _check_request_schema(expected, actual, path, document):
    """Check required nested request fields without rejecting harmless schema detail."""
    problems = []
    if not isinstance(expected, dict):
        return problems
    actual = _resolve_openapi_ref(actual, document)
    if isinstance(actual, dict) and actual.get("anyOf"):
        variants = [variant for variant in actual["anyOf"] if variant.get("type") != "null"]
        if variants:
            return _check_request_schema(expected, variants[0], path, document)
    expected_type = expected.get("type")
    if expected_type == "array":
        actual_items = actual.get("items", {}) if isinstance(actual, dict) else {}
        expected_items = expected.get("items", {})
        return _check_request_schema(expected_items, actual_items, f"{path}[]", document)
    if expected_type == "object" or expected.get("properties"):
        actual_properties = actual.get("properties", {}) if isinstance(actual, dict) else {}
        actual_required = set(actual.get("required", [])) if isinstance(actual, dict) else set()
        for name in expected.get("required", []):
            # 分开报告"字段缺失"与"未声明 required"，让 fixer 能对症修而不是盲目重建字段。
            if name not in actual_properties:
                problems.append(f"{path}: required nested field {name} missing")
            elif name not in actual_required:
                problems.append(f"{path}: nested field {name} exists but must be declared required")
        for name, child in expected.get("properties", {}).items():
            if name in actual_properties:
                problems.extend(_check_request_schema(child, actual_properties[name], f"{path}.{name}", document))
    expected_enum = expected.get("enum")
    if expected_enum and isinstance(actual, dict):
        actual_enum = actual.get("enum", [])
        if not set(expected_enum).issubset(set(actual_enum)):
            problems.append(f"{path}: enum must include {expected_enum}")
    return problems


def check_database(initial_db, final_db, startup=False):
    if not startup:
        return []
    before, after = snapshot_db(initial_db), snapshot_db(final_db)
    return [] if before == after else ["environment changed the database during startup"]


def compact(value, limit=6000):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def trajectory_evidence(trajectory):
    messages = trajectory.get("messages", [])
    return {"messages": compact(messages, 9000), "http_events": compact(trajectory.get("http_events", []), 8000),
            "total_calls": trajectory.get("total_calls", 0), "successful_calls": trajectory.get("successful_calls", 0),
            "tool_catalogue_count": trajectory.get("tool_catalogue_count"),
            "format_violations": trajectory.get("format_violations", 0),
            "error_details": compact(trajectory.get("error_details", []), 3000)}


def scan_trajectory(trajectory):
    errors = list(trajectory.get("database_errors", []))
    for event in trajectory.get("http_events", []):
        result = str(event.get("result", ""))
        if "Status code: 5" in result or "Internal Server Error" in result:
            if len(result) > 5000:
                result = result[:300] + "\n... traceback truncated ...\n" + result[-4700:]
            errors.append(result)
    return {"environment_errors": errors, "review_candidates": []}


def snapshot_diff(initial_db, final_db):
    before, after = snapshot_db(initial_db), snapshot_db(final_db)
    return diff_snapshots(before, after) or None
