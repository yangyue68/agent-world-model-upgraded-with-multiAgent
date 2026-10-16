"""单文件环境子进程入口；stdout 专供 JSON 管道，生成代码的打印写入日志。"""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import traceback
from urllib.parse import quote


def resolve(schema, document, seen=()):
    if isinstance(schema, list):
        return [resolve(s, document, seen) for s in schema]
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if ref and ref not in seen:
        if not ref.startswith("#/"):
            raise ValueError(f"不支持外部 schema 引用: {ref}")
        target = document
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return resolve({**target, **{k: v for k, v in schema.items() if k != "$ref"}}, document, seen + (ref,))
    return {k: resolve(v, document, seen) for k, v in schema.items()}


def catalogue(document):
    routes, tools = {}, []
    for path, path_item in document.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete", "head", "options"):
                continue
            name = operation.get("operationId")
            if not name or name in routes:
                raise ValueError(f"缺少或重复 operationId: {name}")
            params = [resolve(p, document) for p in path_item.get("parameters", []) + operation.get("parameters", [])]
            body = resolve(operation.get("requestBody", {}), document)
            body_schema = resolve(body.get("content", {}).get("application/json", {}).get("schema", {}), document)
            properties, required = {}, []
            for p in params:
                key = p["name"]
                if key in properties:
                    raise ValueError(f"{name}: 不同位置的参数同名，工具输入无法区分: {key}")
                properties[key] = {**p.get("schema", {}), "description": p.get("description", "")}
                if p.get("required"):
                    required.append(key)
            body_shape = body_schema
            branches = [s for s in body_schema.get("anyOf", []) if s.get("type") != "null"]
            if len(branches) == 1:
                body_shape = branches[0]
            flat_body = bool(body_shape.get("properties")) and not (set(properties) & set(body_shape.get("properties", {})))
            if body_schema:
                if flat_body:
                    properties.update(body_shape["properties"])
                    if body.get("required"):
                        required.extend(body_shape.get("required", []))
                else:
                    properties["__body__"] = body_schema
                    if body.get("required"):
                        required.append("__body__")
            schema = {"type": "object", "properties": properties, "required": list(dict.fromkeys(required))}
            if '"$ref"' in json.dumps(schema):
                schema["components"] = document.get("components", {})
            tools.append({"name": name, "description": operation.get("description") or operation.get("summary", ""), "inputSchema": schema})
            routes[name] = (method, path, params, body_shape, flat_body, bool(body.get("required")))
    if not tools:
        raise ValueError("环境没有可调用的业务工具")
    return tools, routes


def invoke(client, routes, request):
    name, arguments = request.get("name"), request.get("arguments")
    event = {"tool_name": name, "arguments": arguments, "status": None, "exception": None}
    if name not in routes:
        return {**event, "status": 404, "result": f"Error: unknown tool '{name}'"}
    if not isinstance(arguments, dict):
        return {**event, "status": 422, "result": "Error: Status code: 422, arguments must be an object"}
    method, path, params, body_schema, flat_body, body_required = routes[name]
    query, headers, cookies = [], {}, {}
    for p in params:
        key, location = p["name"], p["in"]
        if key not in arguments:
            if location == "path":
                return {**event, "status": 422, "result": f"Error: Status code: 422, missing path parameter {key}"}
            continue
        value = arguments[key]
        if location == "path":
            path = path.replace("{" + key + "}", quote(str(value), safe=""))
        elif location == "query":
            for v in value if isinstance(value, list) else [value]:
                query.append((key, json.dumps(v) if isinstance(v, bool) else str(v)))
        elif location == "header":
            headers[key] = str(value)
        elif location == "cookie":
            cookies[key] = str(value)
    payload = {}
    if body_schema:
        if flat_body:
            parameter_names = {p["name"] for p in params}
            body = {k: v for k, v in arguments.items() if k not in parameter_names}
            if body or body_required:
                payload["json"] = body
        elif "__body__" in arguments:
            payload["json"] = arguments["__body__"]
    try:
        client.cookies.clear()
        response = client.request(method.upper(), path, params=query, headers=headers, cookies=cookies,
                                  follow_redirects=False, **payload)
        status = response.status_code
        result = response.text
        if status >= 400:
            result = f"Error: Status code: {status}, {result}"
        return {**event, "status": status, "result": result}
    except Exception:
        error = traceback.format_exc()
        return {**event, "status": 500, "exception": error,
                "result": "Error: Status code: 500, Internal Server Error:\n" + error}


def main():
    channel = sys.stdout
    sys.stdout = sys.stderr
    def send(data):
        channel.write(json.dumps(data, ensure_ascii=False) + "\n")
        channel.flush()
    try:
        # 在生成代码导入之前启用 SQLite 外键约束，SQLAlchemy 与 sqlite3 两种连接均覆盖。
        original_connect = sqlite3.connect
        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        sqlite3.connect = connect
        from sqlalchemy import event
        from sqlalchemy.engine import Engine
        @event.listens_for(Engine, "connect")
        def foreign_keys(connection, _):
            if isinstance(connection, sqlite3.Connection):
                connection.execute("PRAGMA foreign_keys=ON")
        from fastapi import FastAPI
        from fastapi.routing import APIRoute
        from fastapi.testclient import TestClient
        source = Path(sys.argv[1]) / "main.py"
        spec = importlib.util.spec_from_file_location("awm_generated_main", source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if not isinstance(getattr(module, "app", None), FastAPI):
            raise ValueError("main.py 必须导出 FastAPI app")
        with TestClient(module.app, raise_server_exceptions=True) as client:
            # OpenAPI 会覆盖重复路由，先从真实路由表检查，避免检查与实际调用不是同一个函数。
            seen_routes, seen_ids = set(), set()
            for route in module.app.routes:
                if not isinstance(route, APIRoute) or not route.include_in_schema:
                    continue
                name = route.operation_id or route.unique_id
                if name in seen_ids:
                    raise ValueError(f"重复 operationId：{name}")
                seen_ids.add(name)
                for method in route.methods:
                    key = (method, route.path)
                    if key in seen_routes:
                        raise ValueError(f"重复路由：{method} {route.path}")
                    seen_routes.add(key)
            document = module.app.openapi()
            tools, routes = catalogue(document)
            send({"ok": True, "openapi": document, "tools": tools})
            for line in sys.stdin:
                send(invoke(client, routes, json.loads(line)))
    except Exception:
        send({"ok": False, "error": traceback.format_exc()})


if __name__ == "__main__":
    main()
