# ============================================================
# 本文件的职责：
#   项目自带的"实用工具函数库"，供其它模块复用，主要包括：
#   - 数据库 dump（把 SQLite 数据库导出成 SQL 文本）
#   - 端口/服务器相关（随机端口、等待服务器就绪、杀进程）
#   - 环境变量隔离（isolated_mcp_env）
#   - MCP 服务器检查（check_mcp_server）
#   - JSON/JSONL 读写与稳健解析、token 计数、URL/场景名规范化等。
# ============================================================

import re                 # 正则表达式：场景名规范化等
import os                 # 操作系统工具：文件、环境变量
import orjson             # 高性能 JSON 解析库
import json_repair        # 修复损坏的 JSON 字符串
import json               # 标准 JSON 库
import time               # 时间工具：等待、休眠
import socket             # 网络套接字：端口检测
import subprocess         # 子进程：杀端口进程等
import asyncio            # 异步 I/O：等待服务器就绪
import contextlib         # 上下文管理器：重定向 stderr、环境变量隔离
import io                 # 内存流：StringIO 重定向
import random             # 随机：随机端口

from tiktoken import encoding_for_model  # OpenAI token 计数
from json_repair import repair_json      # JSON 修复
from pathlib import Path                 # 路径处理
from loguru import logger                # 日志工具
from lib.interrupt import cancellable, check_cancelled, interruptible_sleep
from mcp import ClientSession              # mcp 官方客户端（与 mcp 1.x 协议匹配）
# 兼容新旧 mcp：旧版函数名 streamablehttp_client，新版改名 streamable_http_client
try:
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client


def dump_sqlite_to_string(db_path: str) -> str:
    # 把 SQLite 数据库导出成 SQL 文本（先试 iterdump，失败则手动导出）。
    from contextlib import closing
    import sqlite3
    
    with closing(sqlite3.connect(db_path)) as conn:  # 打开数据库连接（离开 with 自动关闭）
        conn.text_factory = str  # 文本按 str 处理，避免 bytes

        try:
            return "\n".join(conn.iterdump())  # 优先用 iterdump 完整导出
        except sqlite3.Error as e:
            logger.error(f"{db_path} [dump_sqlite_to_string] iterdump failed: {e!r}, fallback to manual dump.")  # iterdump 失败则手动导出

        lines: list[str] = []  # 存放导出的 SQL 语句
        cur = conn.cursor()    # 创建游标

        lines.append("BEGIN TRANSACTION;")  # 手动导出：开始事务

        # 手动导出：遍历所有表，拼 CREATE TABLE 和 INSERT 语句
        for name, sql in cur.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type='table'
              AND name NOT LIKE 'sqlite_%'
              AND sql IS NOT NULL
            ORDER BY name
            """
        ):
            lines.append(f"{sql};")  # 建表语句

            try:
                data_cur = conn.execute(f'SELECT * FROM "{name}"')  # 查该表所有数据
                col_count = len(data_cur.description)  # 列数

                for row in data_cur:
                    values_sql = []
                    for v in row:
                        if v is None:
                            values_sql.append("NULL")  # NULL 值
                        elif isinstance(v, (int, float)):
                            values_sql.append(str(v))  # 数字直接转字符串
                        else:
                            q = conn.execute("SELECT quote(?)", (v,)).fetchone()[0]  # 其它类型用 quote 转义
                            values_sql.append(q)

                    values_str = ",".join(values_sql)  # 拼成一行值
                    lines.append(f'INSERT INTO "{name}" VALUES({values_str});')  # 插入语句

            except sqlite3.Error as e:
                lines.append(f"-- error dumping data for table {name}: {e!r}")  # 导出失败记注释

        # 导出索引、触发器、视图
        for type_ in ("index", "trigger", "view"):
            for name, sql in cur.execute(
                """
                SELECT name, sql
                FROM sqlite_master
                WHERE type = ?
                  AND name NOT LIKE 'sqlite_%'
                  AND sql IS NOT NULL
                ORDER BY name
                """,
                (type_,),
            ):
                lines.append(f"{sql};")

        lines.append("COMMIT;")  # 提交事务
        dump_str = "\n".join(lines)  # 拼成完整文本

        logger.debug(f"{db_path} [dump_sqlite_to_string] manual dump done, {len(lines)} lines.\n{dump_str[:1000]}")  # 打印前 1000 字符供调试

        return dump_str


def get_random_available_port(start_port: int | None = None, end_port: int | None = None, max_attempts: int = 100) -> int:
    # 获取一个可用的随机端口（不指定范围则让系统自动分配）。
    
    if start_port is None or end_port is None:
        # 未指定范围：绑定端口 0，让操作系统分配一个空闲端口
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            s.listen(1)
            port = s.getsockname()[1]  # 取系统分配的端口号
        return port
    
    for _ in range(max_attempts):
        # 在指定范围内随机尝试最多 max_attempts 次
        port = random.randint(start_port, end_port - 1)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # 允许端口复用
                s.bind(('', port))   # 尝试绑定
                s.listen(1)
            return port  # 绑定成功即返回
        except OSError:
            continue  # 端口被占用则继续尝试
    
    raise RuntimeError(f"no available port found in range {start_port}-{end_port} after {max_attempts} attempts")  # 尝试耗尽则报错

@contextlib.contextmanager
def isolated_mcp_env():
    # 上下文管理器：临时隔离环境变量（只保留安全前缀和指定 key），退出后恢复。
    MCP_ENV_SAFE_PREFIXES = ['HOME', 'USER', 'PATH', 'LANG', 'TERM', 'SHELL', 'PWD', 'TMPDIR', 'TMP', 'TEMP',]  # 安全前缀
    MCP_ENV_SAFE_KEYS = ['PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV']  # 安全 key

    saved_env = dict(os.environ)  # 备份当前所有环境变量
    
    keys_to_remove = []
    for key in os.environ.keys():
        is_safe = any(key.startswith(prefix) for prefix in MCP_ENV_SAFE_PREFIXES) or key in MCP_ENV_SAFE_KEYS
        if not is_safe:
            keys_to_remove.append(key)  # 不安全的 key 记录下来
    
    for key in keys_to_remove:
        del os.environ[key]  # 删除不安全的环境变量
    
    try:
        yield  # 进入 with 块体
    finally:
        # 退出时恢复所有环境变量
        os.environ.clear()
        os.environ.update(saved_env)

async def check_mcp_server(url: str, timeout: float = 10.0) -> tuple[bool, int, list[dict], str | None]:
    """
    return (is_running, tools_count, tools, error)
    """
    # 检查 MCP 服务器是否运行：连接并列出其工具，返回 (是否运行, 工具数, 工具列表, 错误信息)。

    # 用 mcp 官方客户端（与 mcp 1.x 协议匹配）连接并列出工具。
    # 注意：不用 mcp_agent（0.2.6 与 mcp 1.29.0 协议不兼容，list_tools 会返回空 tools）。
    try:
        async with streamablehttp_client(url, timeout=timeout) as (read, write, _):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=timeout)  # 初始化握手
                tools_result = await asyncio.wait_for(session.list_tools(), timeout=timeout)  # 列出工具（带超时）

                tools = []
                for t in tools_result.tools:
                    # 把每个工具的信息整理成字典
                    tool_info = {
                        "name": t.name,
                        "description": t.description or "",
                        "inputSchema": t.inputSchema or {},
                    }
                    if getattr(t, "title", None):
                        tool_info["title"] = t.title
                    if getattr(t, "outputSchema", None):
                        tool_info["outputSchema"] = t.outputSchema
                    if getattr(t, "annotations", None):
                        tool_info["annotations"] = t.annotations.model_dump_json()
                    if getattr(t, "meta", None):
                        tool_info["meta"] = t.meta

                    tools.append(tool_info)

                if len(tools) == 0:
                    return (False, 0, [], "No tools available from MCP server")  # 没有工具视为失败
                return (True, len(tools), tools, None)  # 成功返回
    except asyncio.TimeoutError:
        return (False, 0, [], f"Timeout after {timeout}s")  # 超时
    except Exception as e:
        # asyncio.TaskGroup 失败时抛 ExceptionGroup（Python 3.11+），会掩盖真实子异常；
        # 拆开取第一个子异常，否则错误信息只显示 "unhandled errors in a TaskGroup (1 sub-exception)"，
        # 看不出是连接被拒还是 server 崩了。
        if isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        return (False, 0, [], str(e))  # 其它异常

async def async_wait_for_server(port: int, timeout: float = 60.0) -> bool:
    """Wait for an MCP server to become available on the given port."""
    # 异步等待 MCP 服务器在指定端口就绪。
    start_time = time.time()
    last_err = None
    while time.time() - start_time < timeout:
        check_cancelled()
        # 超时时间内反复探测
        try:
            running, tools_count, tools, err = await cancellable(check_mcp_server(
                url=f"http://localhost:{port}/mcp", timeout=10
            ))
            if running and tools_count and len(tools) > 0:
                return True  # 服务器就绪且有工具
            else:
                last_err = err
        except Exception:
            pass  # 探测异常忽略，继续等待
        await cancellable(asyncio.sleep(0.5))  # 保留探测间隔，同时响应取消。

    if last_err:
        logger.error(f"Failed to connect to MCP server after {timeout}s: {last_err}")  # 超时后打印错误
    return False


def wait_for_server(port: int, timeout: float = 60.0) -> bool:
    """Sync wrapper around async_wait_for_server."""
    # async_wait_for_server 的同步包装。
    return asyncio.run(async_wait_for_server(port, timeout=timeout))

def kill_process_on_port(port):
    # 杀掉占用指定端口的进程（仅类 Unix 环境，用 lsof + kill）。
    try:
        result = subprocess.check_output(f"lsof -t -i:{port}", shell=True)  # 查找占用端口的 PID
        pids = result.decode().strip().split()
        for pid in pids:
            print(f"Killing PID {pid} on port {port}...")
            os.system(f"kill -9 {pid}")  # 强制杀进程
    except subprocess.CalledProcessError:
        print(f"No process found on port {port}.")  # 没有进程占用该端口

def is_port_available(port):
    # 判断端口是否可用（尝试绑定即知）。
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('localhost', port))  # 绑定成功说明可用
        interruptible_sleep(0.1)
        return True
    except OSError:
        return False  # 绑定失败说明被占用

def wait_port_free(port, timeout=10) -> bool:
    # 等待端口释放；超时后尝试杀进程并递归继续等待。
    if timeout < 1:
        return False
    start = time.time()
    while time.time() - start < timeout:
        check_cancelled()
        if is_port_available(port):
            return True  # 端口已可用
        interruptible_sleep(0.2)  # 每 0.2 秒检查一次
    kill_process_on_port(port)  # 超时则强杀占用进程
    return wait_port_free(port, timeout // 2)  # 减半超时递归等待

def _sanitize_for_json_utf8(obj):
    # 递归把对象里的字符串做 UTF-8 清洗，避免非法字符导致 JSON 序列化失败。
    if isinstance(obj, dict):
        return {_sanitize_for_json_utf8(k): _sanitize_for_json_utf8(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_json_utf8(item) for item in obj]
    elif isinstance(obj, str):
        return obj.encode('utf-8', errors='surrogatepass').decode('utf-8', errors='replace')  # 处理非法代理对
    elif isinstance(obj, tuple):
        return tuple(_sanitize_for_json_utf8(item) for item in obj)
    else:
        return obj


def format_db_schema(db_schema: dict) -> str:
    # 把数据库表结构（dict）格式化成可读文本（建表 DDL + 索引），供提示词使用。
    parts = ["Database Schema (SQLite3):", ""]  # 输出开头标题
    for table in db_schema.get("tables", []):
        ddl = table.get("ddl", "").strip()  # 建表语句
        if ddl:
            parts.append(ddl)
            parts.append("")

        indexes = table.get("indexes", [])  # 索引列表
        for idx in indexes:
            if not isinstance(idx, str):
                logger.warning(f"Invalid index: {idx}, type: {type(idx)}, force to string...")
                idx = str(idx)  # 非字符串索引强制转字符串
            idx_stmt = idx.strip()
            if idx_stmt:
                parts.append(idx_stmt)  # 加入索引语句
        if indexes:
            parts.append("")

    while parts and parts[-1] == "":
        parts.pop()  # 去掉末尾多余的空行

    return "\n".join(parts)  # 拼成文本返回


def tools_robust_json_loads(s: str) -> dict:
    # 稳健地解析 JSON：支持文件路径、空串、列表，依次尝试 orjson / json / json_repair。
    if isinstance(s, Path):
        s = s.as_posix()  # Path 对象转字符串路径
        is_path = True
    else:
        is_path = False

    if isinstance(s, str) and os.path.exists(s) and (s.endswith('.json') or s.endswith('.jsonl')) or is_path:
        s = open(s, 'r').read()  # 若 s 是文件路径，则读取文件内容
    
    if isinstance(s, str) and s.strip() == "":
        return {}  # 空字符串返回空字典

    if isinstance(s, list):
        return [tools_robust_json_loads(item) for item in s]  # 列表则逐个递归解析
    
    s = s.encode('utf-8', errors='ignore').decode('utf-8')  # 清洗编码
    s = s.strip()  # 去除首尾空白

    try:
        return orjson.loads(s)  # 优先用 orjson
    except Exception:
        pass
    
    try:
        return json.loads(s)  # 再用标准 json
    except Exception:
        pass


    s = _sanitize_for_json_utf8(s)  # 进一步清洗非法字符

    decoded = json_repair.loads(s)  # 最后用 json_repair 修复并解析

    if decoded == "":
        return {}
    
    return decoded

def normalize_scenario_name(scenario: str) -> str:
    # 规范化场景名：小写、非字母数字下划线转下划线、合并连续下划线并去首尾。
    s = scenario.lower()  # 转小写
    s = re.sub(r'[^a-z0-9_]', '_', s)  # 非法字符 -> 下划线
    s = re.sub(r'_+', '_', s).strip('_').strip()  # 合并重复下划线并去首尾
    return s


def safe_dir_name(name: str) -> str:
    # 把任意名字转成安全的目录名：空格与 Windows 非法字符替换为连字符，去首尾，空名兜底。
    # 保留大小写与中文等可读字符（比 normalize_scenario_name 更可读），只把不能出现在目录名里的字符归一化成连字符。
    s = re.sub(r'[<>:"/\\|?*\s]+', '-', str(name or '').strip())
    s = s.strip('-').strip()
    return s or 'unnamed'

def tools_jsonl_load(filepath):
    # 读取 JSONL 文件，返回每行一个 JSON 对象的列表。
    with open(filepath, 'r', encoding='utf-8') as f:  # 明确指定 UTF-8 编码
        return [json.loads(line) for line in f.readlines()]

def tools_jsonl_save(data: list[dict], path: str, append: bool = False):
    # 把 dict 列表保存为 JSONL（每行一个 JSON）。
    os.makedirs(os.path.dirname(path), exist_ok=True)  # 确保目录存在
    mode = 'a' if append else 'w'  # 追加或覆盖
    with open(path, mode, encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')  # 每行写一个 JSON

def tools_token_count(text: str, model: str) -> int:
    # 用 tiktoken 计算文本 token 数；失败则退化为字符长度。
    try:
        enc = encoding_for_model(model)  # 获取模型对应的编码器
        return len(enc.encode(text))
    except Exception:
        logger.warning(f"Failed to get token count for model {model}, fallback to length of text")
        return len(text)  # 退化为字符长度


def json_default(obj):
    # json.dump 的 default 回调：无法序列化的对象转成字符串。
    return str(obj)

def tools_json_save(data: dict | list, path: str):
    # 把数据保存为 JSON 文件；失败则用 repair_json 修复后再保存。
    with open(path, 'w', encoding='utf-8', errors='replace') as f:
        try:
            json.dump(data, f, indent=4, ensure_ascii=False, default=json_default)  # 正常保存
        except Exception as e:
            print(f"Failed to save JSON to {path}: {e}, fallback to repair_json")
            data = repair_json(str(data), return_objects=True)  # 修复后重试
            json.dump(data, f, indent=4, ensure_ascii=False, default=json_default)


def normalize_azure_url(base_url: str) -> str:
    """Normalize Azure OpenAI base URLs to include /openai/v1 suffix.
    After normalization, we can directly use the OpenAI client to interact with the Azure OpenAI endpoint.
    """
    # 规范化 Azure OpenAI 的 base_url：补上 /openai/v1 后缀，使其可直接用 OpenAI 客户端访问。
    if "openai.azure.com" in base_url or "services.ai.azure.com" in base_url:
        stripped = base_url.rstrip("/")  # 去掉末尾斜杠
        if not stripped.endswith("/openai/v1"):
            return stripped + "/openai/v1"  # 补后缀
    return base_url


def resolve_llm_config(
    api_url_override: str | None = None,
    model_override: str | None = None,
    require_model: bool = False,
) -> tuple[str, str, str]:
    """Resolve LLM API URL, API key, and model from environment variables.

    Supports Azure OpenAI and standard OpenAI providers via env vars:
      - AWM_SYN_LLM_PROVIDER ("azure" | "openai")
      - AZURE_ENDPOINT_URL / AZURE_OPENAI_API_KEY  (for azure)
      - OPENAI_BASE_URL / OPENAI_API_KEY            (for openai)
      - AWM_SYN_OVERRIDE_MODEL                       (model name)

    Args:
        api_url_override: if given, used directly as base_url (still normalized for Azure).
        model_override: if given, used instead of AWM_SYN_OVERRIDE_MODEL env var.
        require_model: if True, raises ValueError when no model can be resolved.

    Returns:
        (api_url, api_key, model)
    """
    # 从环境变量解析 LLM 的 API 地址、key 和模型名，返回 (api_url, api_key, model)。
    provider = os.environ.get("AWM_SYN_LLM_PROVIDER", "").lower()  # provider（azure/openai）

    # --- 解析 base URL ---
    if api_url_override:
        api_url = normalize_azure_url(api_url_override)  # 有覆盖则直接用
    elif provider == "azure":
        azure_endpoint = os.environ.get("AZURE_ENDPOINT_URL", "")
        if not azure_endpoint:
            raise ValueError(
                "AZURE_ENDPOINT_URL not set.\n"
                "Please set: AWM_SYN_LLM_PROVIDER, AZURE_ENDPOINT_URL, AZURE_OPENAI_API_KEY"
            )
        api_url = normalize_azure_url(azure_endpoint)  # Azure 端点规范化
    elif os.environ.get("OPENAI_BASE_URL"):
        api_url = os.environ["OPENAI_BASE_URL"]  # 用 OpenAI 自定义 base url
    else:
        raise ValueError("No LLM API URL provided.")  # 没有可用地址

    # --- 解析 API key ---
    if provider == "azure":
        api_key = os.environ.get("AZURE_OPENAI_API_KEY", "EMPTY")  # Azure key
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "EMPTY")  # OpenAI key

    # --- 解析模型名 ---
    model = model_override or os.environ.get("AWM_SYN_OVERRIDE_MODEL", "")  # 覆盖优先，其次环境变量
    if not model:
        if require_model:
            raise ValueError("AWM_SYN_OVERRIDE_MODEL not set and no model override provided")

    return api_url, api_key, model


def sanitize_for_json(obj):
    """Recursively sanitize an object so it can be safely serialized to JSON."""
    # 递归清洗对象，使其能安全地 JSON 序列化。
    if isinstance(obj, dict):
        # 字典：key 若是元组则转字符串，value 递归清洗
        return {str(k) if isinstance(k, tuple) else k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [sanitize_for_json(item) for item in obj]  # 列表/元组逐个递归
    elif isinstance(obj, bytes):
        return obj.decode("utf-8", errors="ignore")  # bytes 解码
    else:
        try:
            json.dumps(obj)  # 测试能否序列化
            return obj
        except TypeError:
            return str(obj)  # 不能序列化就转字符串


def find_scenario_entry(
    data: list[dict],
    scenario: str,
    task_idx: int | None = None,
) -> dict | None:
    """Find an entry in a JSONL-loaded list by normalized scenario name and optional task_idx.
    Returns:
        The first matching dict, or None.
    """
    # 在 JSONL 加载的列表中按规范化场景名（和可选 task_idx）查找条目。
    scenario_norm = normalize_scenario_name(scenario)  # 规范化场景名
    for entry in data:
        if normalize_scenario_name(entry.get("scenario", "")) != scenario_norm:
            continue  # 场景名不匹配跳过
        if task_idx is not None and entry.get("task_idx") != task_idx:
            continue  # task_idx 不匹配跳过
        return entry  # 找到第一个匹配项
    return None


if __name__ == "__main__":
    pass  # 直接运行时无操作（本模块只作为工具库被导入使用）
