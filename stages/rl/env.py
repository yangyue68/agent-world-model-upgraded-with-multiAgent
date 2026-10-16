# -*- coding: utf-8 -*-
"""RL 环境入口：新版单文件环境走独立进程 ASGI，旧多文件环境保留进程内兼容。

对比旧 rl/env.py（MCPEnvironment）：
  旧：agent → HTTP JSON-RPC → uvicorn 子进程 → FastApiMCP → FastAPI 路由
  新：agent → 直接 await FastAPI 路由函数（进程内、无 HTTP、无子进程）

关键点：
  1. env 生成代码是纯 FastAPI，完全不认识 MCP（MCP 只是运行时由 run_mcp.py 临时挂的），
     所以 env 产物无需重新生成，直接复用。
  2. 每个 rollout 有独立 db：不复用 env 里 database.py 的全局 engine（它绑 DATABASE_PATH 环境变量），
     而是给每个 rollout 新建 engine 绑定自己的 final.db，调用路由时注入 db=session。
  3. 工具名用 operation_id（与 FastApiMCP 一致），保证 agent 学到的工具名不变。
"""

import asyncio
import datetime
import gc
import hashlib
import inspect
import json
import os
import shutil
import sys
import threading
import traceback
import types
import typing
from pathlib import Path

from pydantic import BaseModel
from pydantic_core import PydanticUndefined
from loguru import logger
from sqlalchemy.exc import IntegrityError

# 复用 lib.runtime 的 db 重建（建干净库 + 保存 initial.db 快照）
from lib.runtime import ServerConfig, _prepare_database
from lib.interrupt import cancellable, check_cancelled
from lib.tools import normalize_scenario_name

# 定位 ma_upgrade 目录（env.py 上溯三级：rl → stages → ma_upgrade）
_MA_ROOT = Path(__file__).resolve().parent.parent.parent
_PROJECT_ROOT = _MA_ROOT
if str(_MA_ROOT) not in sys.path:
    sys.path.insert(0, str(_MA_ROOT))


# ============================================================
# 场景级 env app 缓存 + 模块隔离
# ============================================================
# 不同场景的 env 代码都有同名的 main/database/models/schemas/routers 模块，
# 同时 import 会互相覆盖（sys.modules 同名冲突）。所以：
#   1. 记录「已加载的 env 模块名」，切换场景时先清理，再 import 新场景；
#   2. 每个「场景 + env 代码目录 + 代码指纹」的 (op_to_func, tools, Base) 缓存起来，同场景同代码的
#      多条 rollout 共享，避免重复 import。
#
# ★ 缓存 key 必须带上「env 代码目录 + 代码指纹」，不能只用场景名：
#   env 阶段是「每个任务一份独立代码包」，同场景的不同任务代码完全不同。只按场景名缓存会导致
#   task_2 的探针实际跑的是 task_1 的代码（实测：报错栈里出现 ..._task_1/routers/users.py），
#   表现为 no such column 之类的 5xx 永远修不掉、任务被大量重试后淘汰。
#   代码指纹则保证 fixer 改完代码后缓存自动失效（否则一直跑旧代码）。
_loaded_env_module_names: set[str] = set()
_loaded_env_dirs: list[str] = []          # 已插入 sys.path 的 env 代码目录（切换时摘掉，防止无限增长）
_scenario_cache: dict[str, tuple] = {}
_scenario_cache_max = 16                  # 缓存上限：超过就整体清空（避免长跑时内存里堆积 app/Base）
# env 代码使用同名的 main/database/models 模块，并且不少生成路由会直接调用
# database.SessionLocal()。因此 import、切换 sys.modules、绑定 SessionLocal 和调用
# 路由必须使用同一把可重入锁；分成两把锁会出现 A 正在调用时 B 清理了 A 的模块。
_env_lock = threading.RLock()
_session_bind_lock = _env_lock


def _clear_loaded_env_modules() -> None:
    """清理已加载的 env 模块，并把上一条 env 代码目录从 sys.path 摘掉（避免切换场景时同名模块冲突）。"""
    for name in list(_loaded_env_module_names):
        sys.modules.pop(name, None)
    _loaded_env_module_names.clear()
    for d in _loaded_env_dirs:
        try:
            sys.path.remove(d)
        except ValueError:
            pass
    _loaded_env_dirs.clear()


def _env_code_fingerprint(env_dir: str) -> str:
    """env 代码包指纹：所有 .py 文件的「相对路径 + 大小 + mtime_ns」的 md5 前 12 位。

    用于让 app 缓存在代码被 fixer 改过之后自动失效（否则探针会一直跑旧代码，问题永远修不掉）。
    目录里的 .py 文件只有几个，开销可以忽略。
    """
    items = []
    if not os.path.isdir(env_dir):
        return "missing"
    for dirpath, dirs, files in os.walk(env_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            items.append(f"{os.path.relpath(fp, env_dir)}:{st.st_size}:{st.st_mtime_ns}")
    return hashlib.md5("|".join(sorted(items)).encode("utf-8")).hexdigest()[:12]


def _remember_env_modules(env_dir_abs: str) -> None:
    """记录本次加载、且位于 env_dir 下的模块名（只清理 env 自己的模块，不动第三方库）。"""
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if f and os.path.abspath(f).startswith(env_dir_abs):
            _loaded_env_module_names.add(name)


def _route_maps(app):
    """OpenAPI 自动生成的 operationId 使用 unique_id，必须与工具列表一致。"""
    from fastapi.routing import APIRoute

    routes = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        op_id = route.operation_id or route.unique_id
        if op_id in routes:
            raise ValueError(f"重复的 operationId：{op_id}")
        routes[op_id] = route
    return {name: route.endpoint for name, route in routes.items()}, routes


def _resolve_schema(schema, openapi, seen=()):
    """展开 OpenAPI 本地引用，保留 nullable、枚举、数组和嵌套对象等约束。"""
    if isinstance(schema, list):
        return [_resolve_schema(item, openapi, seen) for item in schema]
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if ref:
        if not ref.startswith("#/"):
            raise ValueError(f"不支持的 OpenAPI 外部引用：{ref}")
        if ref in seen:
            # 自引用模型保持引用；工具 schema 同时携带 components，引用仍可解析。
            return dict(schema)
        target = openapi
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        merged = {**target, **{key: value for key, value in schema.items() if key != "$ref"}}
        return _resolve_schema(merged, openapi, seen + (ref,))
    return {key: _resolve_schema(value, openapi, seen) for key, value in schema.items()}


def _build_tools_from_openapi(app, op_to_func: dict) -> list[dict]:
    """从 OpenAPI 构建工具清单（name=operationId, description=summary/description, inputSchema=参数）。

    与 FastApiMCP 的命名/描述对齐，保证 agent 拿到的工具名与参数不变。
    """
    tools = []
    try:
        openapi = app.openapi()
    except Exception as e:
        logger.warning(f"openapi() 生成失败，工具清单为空: {e}")
        return []
    for path_item in openapi.get("paths", {}).values():
        for method, op in path_item.items():
            if method.lower() not in ("get", "post", "put", "delete", "patch"):
                continue
            op_id = op.get("operationId")
            if not op_id or op_id not in op_to_func:
                continue
            description = op.get("description") or op.get("summary") or ""
            properties, required = {}, []
            for p in op.get("parameters", []):
                pname = p.get("name")
                if not pname or pname in ("db", "request", "response", "background_tasks"):
                    continue
                prop = _resolve_schema(p.get("schema", {}), openapi)
                if p.get("description"):
                    prop["description"] = p["description"]
                if p.get("required"):
                    required.append(pname)
                properties[pname] = prop
            # 处理 requestBody（POST/PUT/PATCH 的 body 参数）：OpenAPI 里 body 字段在
            # requestBody.content.application/json.schema，而 parameters 只含 query/path。
            # 漏掉 requestBody 会导致 POST 的 body 参数不进 tool schema，agent 只能靠猜参数名。
            rb = op.get("requestBody", {}) or {}
            rb_schema = ((rb.get("content", {}) or {}).get("application/json", {}) or {}).get("schema", {}) or {}
            rb_schema = _resolve_schema(rb_schema, openapi)
            for bname, bschema in (rb_schema.get("properties", {}) or {}).items():
                if not bname or bname in ("db", "request", "response", "background_tasks"):
                    continue
                bprop = dict(bschema)
                if bname in (rb_schema.get("required", []) or []):
                    required.append(bname)
                properties[bname] = bprop
            input_schema = {"type": "object", "properties": properties, "required": list(dict.fromkeys(required))}
            if '"$ref"' in json.dumps(input_schema):
                input_schema["components"] = openapi.get("components", {})
            tools.append({
                "name": op_id,
                "description": description,
                "inputSchema": input_schema,
            })
    return tools


def _serialize_result(result) -> str:
    """把路由返回值（Pydantic 模型 / dict / list / 标量）转成文本，模拟 MCP 返回的 JSON 文本。"""
    if result is None:
        return "null"
    if hasattr(result, "model_dump"):          # Pydantic v2
        return json.dumps(result.model_dump(), ensure_ascii=False, default=str)
    if hasattr(result, "dict"):                # Pydantic v1
        return json.dumps(result.dict(), ensure_ascii=False, default=str)
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, default=str)
    return str(result)


def _convert_param(value, annotation):
    """把 agent 传的字符串值按类型注解转成正确类型（模拟 FastAPI 的参数解析）。

    进程内直调跳过了 FastAPI 的依赖注入和类型转换，所以这里要手动做：
      - str → 不变
      - int / float → 数值转换
      - bool → "true"/"1"/"yes"/"on" → True
      - date / datetime → fromisoformat 解析（env 里 check_in/check_out 是 date 类型）
      - Optional[X]（Union[X, None]）→ 剥掉 None，按 X 转换
      - Pydantic body 模型 → dict 转成模型实例（模拟 FastAPI 的 body 解析）
      - 其他类型 → 原样返回
    转换失败抛 TypeError/ValueError，由 call_tool 捕获并返回「参数错」。
    """
    if value is None:
        return None
    # 剥掉 Optional[X] / Union[X, None]
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = typing.get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            annotation = non_none[0]

    if annotation is str:
        return str(value)
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)
    if annotation is datetime.date:
        if isinstance(value, str):
            return datetime.date.fromisoformat(value.strip())
        return value
    if annotation is datetime.datetime:
        if isinstance(value, str):
            return datetime.datetime.fromisoformat(value.strip())
        return value
    # Pydantic body 模型（如 update_loyalty_preferences 的 body: LoyaltyPreferencesUpdate）：
    # 进程内直调跳过了 FastAPI 的 body 解析（它会把 JSON dict 自动转成 Pydantic 模型），
    # 这里手动 dict → 模型实例，否则路由里 `body.xxx` 会报 'dict' object has no attribute 'xxx'。
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if isinstance(value, dict):
            return annotation(**value)
        return value
    # 其他类型（自定义、List 等）：原样返回
    return value


def _validate_route_arguments(route, arguments: dict) -> tuple[dict, str | None]:
    """使用 FastAPI 已构建的 ModelField 做参数校验和转换。

    进程内直调如果只看 Python 函数签名，会漏掉 Query(alias=...), ge/le、Path
    约束以及 Pydantic body 的 required/类型规则。这里复用 route.dependant 的
    字段校验，结果与 HTTP 请求保持一致；body 模型仍兼容工具层的扁平 JSON。
    """
    arguments = arguments or {}
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return {}, None
    kwargs: dict = {}
    errors = []

    def validate_field(field, raw, source: str):
        try:
            value, field_errors = field.validate(raw, {}, loc=(source, field.alias))
        except Exception as exc:
            return None, [{"loc": (source, field.alias), "msg": str(exc)}]
        return value, field_errors

    for source, fields in (
        ("path", getattr(dependant, "path_params", [])),
        ("query", getattr(dependant, "query_params", [])),
        ("header", getattr(dependant, "header_params", [])),
        ("cookie", getattr(dependant, "cookie_params", [])),
    ):
        for field in fields:
            key = field.alias or field.name
            if key in arguments:
                raw = arguments[key]
            elif field.name in arguments:
                # 兼容旧 rollout 传 Python 参数名；OpenAPI/正常 agent 优先使用 alias。
                raw = arguments[field.name]
            elif field.required:
                errors.append({"loc": (source, key), "msg": "Field required"})
                continue
            else:
                raw = field.default
            value, field_errors = validate_field(field, raw, source)
            if field_errors:
                errors.extend(field_errors)
            else:
                kwargs[field.name] = value

    body_fields = list(getattr(dependant, "body_params", []) or [])
    if body_fields:
        # 单个 Pydantic body 是工具 schema 的扁平字段集合；若 agent 明确传了
        # 参数名且其值是 dict，也接受嵌套形式。
        if len(body_fields) == 1:
            field = body_fields[0]
            if field.name in arguments and isinstance(arguments[field.name], dict):
                raw = arguments[field.name]
            elif getattr(field, "type_", None) is not None and isinstance(getattr(field, "type_", None), type) \
                    and issubclass(field.type_, BaseModel):
                raw = arguments
            elif field.alias in arguments:
                raw = arguments[field.alias]
            elif field.required:
                errors.append({"loc": ("body", field.alias or field.name), "msg": "Field required"})
                raw = None
            else:
                raw = field.default
            if not (raw is None and field.required):
                value, field_errors = validate_field(field, raw, "body")
                if field_errors:
                    errors.extend(field_errors)
                else:
                    kwargs[field.name] = value
        else:
            for field in body_fields:
                key = field.alias or field.name
                if key in arguments:
                    raw = arguments[key]
                elif field.name in arguments:
                    raw = arguments[field.name]
                elif field.required:
                    errors.append({"loc": ("body", key), "msg": "Field required"})
                    continue
                else:
                    raw = field.default
                value, field_errors = validate_field(field, raw, "body")
                if field_errors:
                    errors.extend(field_errors)
                else:
                    kwargs[field.name] = value

    if errors:
        return {}, json.dumps(errors, ensure_ascii=False, default=str)
    return kwargs, None


class InProcessEnv:
    """进程内直调的环境封装：start() 建 db + 载入 app；list_tools()/call_tool() 直接调路由函数。"""

    def __init__(self, scenario, env_dir, db_schema_path, sample_path, output_dir, task_id=None,
                 initial_db_source=None):
        self.scenario = normalize_scenario_name(scenario)
        self.task_id = task_id                # 任务级 task_id（0-based），None = 场景级兼容
        self.env_dir = env_dir                # env 多文件代码包目录（含 main.py）
        self.db_schema_path = db_schema_path  # 表结构 schema 文件（gen_db_ma.jsonl）
        self.sample_path = sample_path        # 样本数据文件（gen_sample_ma.jsonl）
        self.output_dir = output_dir          # 本环境独立工作目录（放 db）
        self.db_file_path = None              # 工作库 final.db 路径（rollout 后 agent 修改它）
        self.initial_db_path = None           # 初始库快照 initial.db 路径（reward 比对用）
        self._engine = None                   # 本 rollout 的 SQLAlchemy engine（绑 final.db）
        self._session_local = None            # 本 rollout 的 sessionmaker
        self._op_to_func = {}                 # operation_id -> 路由函数
        self._op_to_route = {}                # 保留响应校验配置，工具返回与 HTTP 返回一致
        self._tools = []                      # 工具清单（name/description/inputSchema）
        self._base = None                     # env 的 SQLAlchemy Base（create_all 用）
        self._env_database = None             # env 的 database 模块（call_tool 里临时替换 SessionLocal 用）
        self._env_modules: dict[str, object] = {}  # 本环境同名模块快照，调用时恢复到 sys.modules
        self.initial_db_source = initial_db_source
        self._loaded = False
        self._single_file_env = None

    # ---------- 载入 env app + 工具映射 ----------
    def _cache_key(self) -> str:
        """app 缓存 key = 场景名 + env 代码目录 + 代码指纹（同场景的不同任务代码不同，必须分开缓存）。"""
        # database.py 在 import 时读取 DATABASE_PATH；不同 rollout 的 final.db 不同，
        # 不能让后一个 rollout 复用前一个 rollout 的全局 engine/SessionLocal。
        return (f"{self.scenario}::{os.path.abspath(self.env_dir)}::"
                f"{os.path.abspath(self.db_file_path or '')}::{_env_code_fingerprint(self.env_dir)}")

    def _load_env_app(self):
        """import env 的 main.py，建立 operation_id -> 函数 映射 + 工具清单（按「场景+目录+代码指纹」缓存）。"""
        key = self._cache_key()
        with _env_lock:  # 串行化：避免多条 rollout 并发清理/import 导致 sys.modules 竞争
            if key in _scenario_cache:
                (self._op_to_func, self._op_to_route, self._tools, self._base,
                 self._env_database, self._env_modules) = _scenario_cache[key]
                self._loaded = True
                return

            env_dir_abs = os.path.abspath(self.env_dir)
            # 清掉上一条 env 代码残留的同名模块，避免 main/database/models 冲突
            _clear_loaded_env_modules()
            if env_dir_abs not in sys.path:
                sys.path.insert(0, env_dir_abs)
            _loaded_env_dirs.append(env_dir_abs)

            try:
                import main as env_main  # type: ignore[import-not-found]
                import database as env_database  # type: ignore[import-not-found]
                app = env_main.app
                base = env_database.Base

                # operation_id -> 路由函数
                op_to_func, op_to_route = _route_maps(app)
                tools = _build_tools_from_openapi(app, op_to_func)
            except Exception:
                # 导入失败时也要清掉半加载模块，否则下一任务会复用损坏的同名模块。
                _clear_loaded_env_modules()
                raise

            _remember_env_modules(env_dir_abs)
            env_modules = {
                name: sys.modules[name] for name in _loaded_env_module_names
                if name in sys.modules
            }
            if len(_scenario_cache) >= _scenario_cache_max:
                _scenario_cache.clear()  # 长跑（几千个任务）时避免缓存无限堆积
            _scenario_cache[key] = (op_to_func, op_to_route, tools, base, env_database, env_modules)
            (self._op_to_func, self._op_to_route, self._tools, self._base,
             self._env_database, self._env_modules) = (
                op_to_func, op_to_route, tools, base, env_database, env_modules
            )
            self._loaded = True
            logger.info(f"{self.scenario}: 进程内载入 env app（{env_dir_abs}），{len(self._tools)} 个工具（无 MCP/子进程）")

    # ---------- 生命周期 ----------
    def start(self):
        """重建 db + 载入 env app + 建本 rollout 的 engine（每个 rollout 独立 db）。"""
        check_cancelled()
        marker = Path(self.env_dir) / "environment.json"
        from lib.single_file_env import ENV_VERSION, SingleFileEnv
        metadata = None
        if marker.is_file():
            try:
                metadata = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"{self.scenario}: invalid environment metadata in {marker}: {exc}")
        is_single_file = bool(metadata and metadata.get("env_version") == ENV_VERSION)
        if not is_single_file and (Path(self.env_dir) / "main.py").is_file() and not (Path(self.env_dir) / "database.py").is_file():
            is_single_file = True
        if is_single_file:
            source = self.initial_db_source
            if source:
                source = str((Path(_MA_ROOT) / source).resolve())
            else:
                if self.task_id is None:
                    raise ValueError("单文件任务环境必须提供 task_id 或 initial_db_source，不能回退到场景首个数据库")
                # 沿用训练侧按精确任务键构建初始库的入口。
                cfg = ServerConfig(scenario=self.scenario, envs_load_path="",
                                   db_schema_path=self.db_schema_path, sample_path=self.sample_path,
                                   task_id=self.task_id)
                seed_dir = os.path.join(self.output_dir, "_seed")
                os.makedirs(seed_dir, exist_ok=True)
                source = _prepare_database(cfg, seed_dir)
            self._single_file_env = SingleFileEnv(self.scenario, self.env_dir, self.output_dir, source)
            self._single_file_env.start()
            self.initial_db_path = self._single_file_env.initial_db_path
            self.db_file_path = self._single_file_env.db_file_path
            return
        os.makedirs(self.output_dir, exist_ok=True)
        # 1. 用 schema+sample 重建干净库 + 保存 initial.db 快照（复用 lib.runtime）
        server_cfg = ServerConfig(
            scenario=self.scenario,
            envs_load_path="",
            db_schema_path=self.db_schema_path,
            sample_path=self.sample_path,
            task_id=self.task_id,
        )
        self.initial_db_path = os.path.abspath(os.path.join(self.output_dir, "initial.db"))
        self.db_file_path = os.path.abspath(os.path.join(self.output_dir, "final.db"))
        if self.initial_db_source:
            source = self.initial_db_source
            if not os.path.isabs(source):
                source = os.path.join(str(_PROJECT_ROOT), source)
            source = os.path.abspath(source)
            if not os.path.exists(source):
                raise FileNotFoundError(f"Initial database not found: {source}")
            shutil.copy2(source, self.initial_db_path)
            shutil.copy2(self.initial_db_path, self.db_file_path)
        else:
            rebuilt = _prepare_database(server_cfg, self.output_dir)
            if os.path.abspath(rebuilt) != self.db_file_path:
                shutil.copy2(rebuilt, self.db_file_path)
            if not os.path.exists(self.initial_db_path):
                shutil.copy2(self.db_file_path, self.initial_db_path)

        # 2. 载入 env app。必须在 import 前注入本 rollout 的 DATABASE_PATH，
        # 否则模板 database.py 会创建项目目录下的 local.db，甚至让路由的
        # `from database import engine` 指向错误数据库。
        db_url = f"sqlite:///{self.db_file_path.replace(chr(92), '/')}"
        with _env_lock:
            old_database_path = os.environ.get("DATABASE_PATH")
            os.environ["DATABASE_PATH"] = db_url
            try:
                self._load_env_app()
            finally:
                if old_database_path is None:
                    os.environ.pop("DATABASE_PATH", None)
                else:
                    os.environ["DATABASE_PATH"] = old_database_path

        # 3. 每个 rollout 独立 engine + sessionmaker（绑定本 rollout 的 final.db）
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        self._engine = create_engine(db_url, connect_args={"check_same_thread": False})
        self._session_local = sessionmaker(autocommit=False, autoflush=False, bind=self._engine)
        # 表已由 _prepare_database 建好，这里 create_all 是 no-op，但兜底确保 models 声明的表存在
        self._base.metadata.create_all(bind=self._engine)

    def list_tools(self) -> list:
        """返回工具清单（供 rollout 里 list_tools 元工具用）。"""
        if self._single_file_env is not None:
            return self._single_file_env.list_tools()
        return self._tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """直接调用路由函数（进程内），返回结果文本。

        约定（与旧 MCP call_tool 对齐）：
          - 成功：返回 JSON 文本
          - 4xx/5xx（HTTPException）：返回 "Error: Status code: {code}, {detail}"
          - 未捕获异常（等价 FastAPI 500）：返回 "Error: Status code: 500, Internal Server Error: ..."
            以便 rollout 里的 has_5xx 检测（"Status code: 5" / "Internal Server Error"）能识别环境 bug。
        """
        check_cancelled()
        if self._single_file_env is not None:
            return self._single_file_env.call_tool(tool_name, arguments)
        func = self._op_to_func.get(tool_name)
        if func is None:
            return f"Error: unknown tool '{tool_name}'"

        arguments = arguments or {}
        sig = inspect.signature(func)
        from fastapi import HTTPException
        route = self._op_to_route.get(tool_name)
        if route is None:
            return f"Error: unknown tool '{tool_name}'"
        kwargs, validation_error = _validate_route_arguments(route, arguments)
        if validation_error:
            return f"Error: Status code: 422, RequestValidationError: {validation_error}"
        # 依赖注入对象由运行时提供；其它非请求参数保持兼容旧环境代码。
        for name, param in sig.parameters.items():
            if name in kwargs or name in ("request", "response", "background_tasks"):
                continue
            if name == "db":
                continue
            if param.default is inspect.Parameter.empty:
                return f"Error: Status code: 422, missing required parameter '{name}'"

        session = None
        if "db" in sig.parameters:
            session = self._session_local()
            kwargs["db"] = session

        # 关键修复：airbnb 等场景的工具函数内部用 `SessionLocal()`（database 模块全局），
        # 而不是 `Depends(get_db)`。database 的全局 engine 绑的是 DATABASE_PATH（默认 xxxx.db 空库），
        # 导致这类工具查空库、返回空结果。
        # 注意：router 里 `from database import SessionLocal` 是「值导入」，指向同一个 sessionmaker 实例，
        # 所以要用 configure(bind=...) 重新绑定，而不是替换 database.SessionLocal 属性。
        # （rl 的 rollout 是顺序执行 call_tool，无并发，临时 configure+恢复是安全的）
        # 但 env 阶段的轨迹 rollout 是「多任务并发」，同一场景的多个 env 会并发 call_tool，
        # 共享同一个 database.SessionLocal，必须加锁串行化 configure+调用+恢复，否则 bind 互相覆盖、db 写错位置。
        with _session_bind_lock:
            # 路由函数可能在调用期间执行 `import models`/`import database`。
            # 恢复本环境自己的同名模块，调用结束后再还原其它任务的模块。
            module_names = set(self._env_modules)
            missing = object()
            saved_modules = {name: sys.modules.get(name, missing) for name in module_names}
            for name, module in self._env_modules.items():
                sys.modules[name] = module
            saved_sys_path = list(sys.path)
            env_dir_abs = os.path.abspath(self.env_dir)
            if env_dir_abs in sys.path:
                sys.path.remove(env_dir_abs)
            sys.path.insert(0, env_dir_abs)
            saved_bind = None
            if self._env_database is not None:
                saved_bind = self._env_database.SessionLocal.kw.get('bind')
                self._env_database.SessionLocal.configure(bind=self._engine)

            try:
                check_cancelled()
                async def invoke():
                    result = func(**kwargs)
                    if inspect.isawaitable(result):
                        result = await result
                    # HTTP 会校验并转换 response_model。进程内也执行同样的校验，
                    # ORM 对象转成字段数据，响应契约错误作为 500 交给 env 修复。
                    from fastapi.routing import serialize_response
                    from starlette.responses import Response

                    if isinstance(result, Response):
                        if result.status_code >= 400:
                            raise HTTPException(status_code=result.status_code, detail=result.body.decode("utf-8", errors="replace"))
                        return result.body.decode("utf-8", errors="replace")
                    if route is not None:
                        return await serialize_response(
                            field=route.response_field, response_content=result,
                            include=route.response_model_include, exclude=route.response_model_exclude,
                            by_alias=route.response_model_by_alias,
                            exclude_unset=route.response_model_exclude_unset,
                            exclude_defaults=route.response_model_exclude_defaults,
                            exclude_none=route.response_model_exclude_none,
                            is_coroutine=True,
                        )
                    return result

                result = asyncio.run(cancellable(invoke()))
                return _serialize_result(result)
            except HTTPException as e:
                return f"Error: Status code: {e.status_code}, {e.detail}"
            except IntegrityError as e:
                # 数据库约束冲突（唯一键重复 / CHECK 约束违反 / 外键缺失）→ 这是 agent 传错参数（如枚举值
                # 大小写错、重复创建已存在记录、依赖字段缺失），不是环境代码 bug。返回 4xx 而非 500，
                # 避免被 rollout 的 has_5xx 检测误判为 env bug（进而误淘汰任务），同时让 agent 能据约束信息修正。
                return f"Error: Status code: 409, IntegrityError: {e.orig or e}"
            except Exception as e:
                # 打印完整 traceback，便于判断是环境代码 bug 还是 agent 传错参数触发
                tb = traceback.format_exc()
                logger.warning(f"[{self.scenario}] 工具 {tool_name} 未捕获异常（traceback）：\n{tb}")
                return f"Error: Status code: 500, Internal Server Error: {type(e).__name__}: {e}\n{tb}"
            finally:
                if self._env_database is not None:
                    # saved_bind 可能本来就是 None，也必须恢复；否则下一次
                    # rollout 会继承本次 final.db 的绑定。
                    self._env_database.SessionLocal.configure(bind=saved_bind)
                if session is not None:
                    session.close()
                sys.path[:] = saved_sys_path
                for name, module in saved_modules.items():
                    if module is missing:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = module

    def _close_leaked_sessions(self) -> None:
        """关掉「绑在本 rollout engine 上、但路由代码没 close()」的 session。

        路由里常见写法是 `db = SessionLocal()`（database 模块全局）而不是 `Depends(get_db)`：
        这类 session 不在连接池里，engine.dispose() 关不到它，其占着的 sqlite 文件句柄会一直留着。
        Windows 上这会让下一次 _prepare_database 的 os.replace 报 [WinError 5] 拒绝访问（实测崩过任务）。
        用 SQLAlchemy 的全局 session 注册表按 bind 精确筛选，只关自己的，不动别的线程的 session。
        """
        if self._engine is None:
            return
        try:
            from sqlalchemy.orm import session as sa_session

            registry = getattr(sa_session, "_sessions", None)
            if registry is None:
                return
            for sess in list(registry.values()):  # 先快照，避免遍历中注册表被修改
                try:
                    if getattr(sess, "bind", None) is self._engine:
                        sess.close()
                except Exception:
                    continue
        except Exception:
            pass

    def close(self):
        """释放 engine（dispose 连接池 + 关掉泄漏 session），无子进程/事件循环需要清理。"""
        if self._single_file_env is not None:
            self._single_file_env.close()
            self._single_file_env = None
            return
        with _session_bind_lock:
            self._close_leaked_sessions()
            engines = []
            if self._engine is not None:
                engines.append(self._engine)
            # database.py 在 import 时创建的全局 engine 也会持有 SQLite 文件句柄。
            # 之前只 dispose 运行时 engine，Windows 上因此出现 final.db WinError 32。
            env_engine = getattr(self._env_database, "engine", None) if self._env_database else None
            if env_engine is not None and env_engine not in engines:
                engines.append(env_engine)
            for engine in engines:
                try:
                    engine.dispose()
                except Exception:
                    pass
            self._engine = None
            self._session_local = None
        gc.collect()  # 让被回收的连接对象立刻释放文件句柄（Windows 上句柄归还得靠对象析构）

