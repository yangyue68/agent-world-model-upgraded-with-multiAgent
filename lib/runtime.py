# -*- coding: utf-8 -*-
"""ma_upgrade 的运行时基础模块（替代对 awm.core.* 的依赖）。

服务器上 ma_upgrade 目录就是项目根，本模块用「相对 ma_upgrade 根」的 import
（from lib.tools / from stages.db.db_utils），不依赖外部的 awm 包。

提供 RL / preflight 需要的三类功能：
  1. 数据库重建：ServerConfig + _prepare_database + reset_single_database + execute_sample_data
  2. MCP 客户端 + XML 工具协议：MCPToolExecutor + get_system_prompt + parse_tool_calls + ...
  3. 验证器：VerificationMode + VerifyConfig + run_verify + run_verifier
"""

import asyncio
import inspect
import json
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from textwrap import dedent

from loguru import logger
from lib.environment_context import CURRENT_USER_ID
from lib.interrupt import interruptible_sleep

from lib.tools import (
    tools_robust_json_loads,
    tools_jsonl_load,
    tools_json_save,
    normalize_scenario_name,
    find_scenario_entry,
    sanitize_for_json,
)
from stages.db.db_utils import create_sqlite_database

from mcp import ClientSession
# 兼容新旧 mcp：旧版函数名 streamablehttp_client，新版改名 streamable_http_client
try:
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client


# ============================================================
# 1. 数据库重建（替代 awm.core.server / awm.core.reset / awm.core.sample）
# ============================================================

@dataclass
class ServerConfig:
    scenario: str
    envs_load_path: str
    db_path: str | None = None
    db_schema_path: str = "./outputs/gen_db.jsonl"
    sample_path: str = "./outputs/gen_sample.jsonl"
    output_dir: str | None = None
    task_id: int | None = None   # 任务级 task_id（0-based），None = 场景级兼容（取该场景第一个任务）


def execute_sample_data(db_path: str, sample_data: dict, scenario_name: str) -> tuple[int, int, set[str]]:
    """把生成的 INSERT 语句真正执行到数据库里，返回 (成功数, 失败数, 错误集合)。"""
    db_file = Path(db_path)
    if not db_file.exists():
        logger.error(f"Database not found: {db_path}, skipping data insertion")
        return 0, 0, {"Database file not found"}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    total_statements = 0
    successful_inserts = 0
    failed_inserts = 0
    error_messages: set[str] = set()

    try:
        for table_data in sample_data.get("tables", []):
            table_name = table_data.get("table_name", "unknown")
            for stmt in table_data.get("insert_statements", []):
                total_statements += 1
                try:
                    cursor.execute(stmt)
                    successful_inserts += 1
                except sqlite3.IntegrityError as e:
                    failed_inserts += 1
                    error_messages.add(f"Table {table_name} - IntegrityError: {str(e)[:1000]}")
                except sqlite3.Error as e:
                    failed_inserts += 1
                    error_messages.add(f"Table {table_name} - SQLError: {str(e)[:1000]}\nStatement: {stmt[:1000]}")

        conn.commit()
        logger.info(f"{scenario_name}: {successful_inserts}/{total_statements} statements executed with {failed_inserts} failures")
    except Exception as e:
        conn.rollback()
        error_messages.add(f"Transaction failed: {str(e)}")
        raise e
    finally:
        conn.close()

    return successful_inserts, failed_inserts, error_messages


def process_schema(schema_item, database_dir, sample_data_dict):
    """处理单个场景：建库 -> 执行插入示例数据 -> 返回 (场景名, 成功数, 失败数, 是否有错误)。"""
    scenario_name = schema_item["scenario"]
    db_schema = schema_item["db_schema"]

    logger.info(f"resetting database for {scenario_name}")

    db_path, _, _, _ = create_sqlite_database(scenario_name, db_schema, database_dir)

    successful = 0
    failed = 0
    has_error = False

    if scenario_name in sample_data_dict:
        sample_data = sample_data_dict[scenario_name]
        try:
            successful, failed, _ = execute_sample_data(db_path, sample_data, scenario_name)
        except Exception as e:
            logger.error(f"Error executing sample data for {scenario_name}: {e}")
            has_error = True
        if failed > 0:
            has_error = True
    else:
        has_error = True
        logger.error(f"No sample data found for {scenario_name}")

    return scenario_name, successful, failed, has_error


def reset_single_database(
    input_db: str,
    input_sample: str,
    scenario: str,
    task_id: int | None = None,
    database_dir: str = "./outputs/databases",
) -> str:
    """从表结构 + 示例数据重建单个任务（或场景）的数据库，返回 .db 文件路径。

    task_id 不为 None 时按 (scenario, task_id) 精确匹配任务级 db/sample 条目，
    否则退回场景级（取该场景第一个匹配条目，兼容旧调用）。
    """
    scenario_norm = normalize_scenario_name(scenario)
    os.makedirs(database_dir, exist_ok=True)

    db_data = tools_jsonl_load(input_db)
    sample_data = tools_jsonl_load(input_sample)

    if task_id is not None:
        task_id_str = f"task_{task_id + 1}"  # 任务级产物用 1-based task_id 字符串（"task_1"）
        schema_item = next((e for e in db_data if normalize_scenario_name(e.get("scenario", "")) == scenario_norm and e.get("task_id") == task_id_str), None)
        sample_item = next((e for e in sample_data if normalize_scenario_name(e.get("scenario", "")) == scenario_norm and e.get("task_id") == task_id_str), None)
    else:
        schema_item = next((e for e in db_data if normalize_scenario_name(e.get("scenario", "")) == scenario_norm), None)
        sample_item = next((e for e in sample_data if normalize_scenario_name(e.get("scenario", "")) == scenario_norm), None)

    if not schema_item:
        raise ValueError(f"Scenario '{scenario}' task {task_id} not found in {input_db}")
    if not sample_item:
        raise ValueError(f"Scenario '{scenario}' task {task_id} not found in {input_sample}")

    sample_data_dict = {sample_item["scenario"]: sample_item["sample_data"]}
    _, successful, failed, has_error = process_schema(schema_item, database_dir, sample_data_dict)

    db_path = os.path.join(database_dir, f"{scenario_norm}.db")
    if not os.path.exists(db_path):
        raise RuntimeError(f"Database file not created: {db_path}")

    if has_error:
        logger.warning(f"Database for {scenario} created with errors (successful={successful}, failed={failed})")

    return db_path


def _replace_or_copy(src: str, dst: str, attempts: int = 4) -> None:
    """把 src 落到 dst（优先原子替换 os.replace，失败退避重试，再失败改用覆盖拷贝）。

    为什么需要这个兜底：Windows 上 os.replace 撞上「被别的文件句柄占着」的 dst
    （上一轮 rollout 残留的 sqlite 连接、杀软扫描、索引服务）会直接抛
    OSError: [WinError 5] 拒绝访问。env 阶段实测：3 个任务的 run 里有 2 个就是这么被打崩的，
    而且异常一路逃逸到线程池，连未清理的产物都留在了磁盘上（假产物）。
    拷贝是「覆盖写」，不需要删除/移动目标文件，受阻概率低得多；拷贝成功后再尽力删掉源文件。
    """
    last_err: OSError | None = None
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except OSError as e:
            last_err = e
            interruptible_sleep(0.2 * (attempt + 1))  # 退避：给杀软/上一个句柄释放的时间

    try:
        shutil.copyfile(src, dst)
    except OSError as e:
        raise RuntimeError(
            f"无法生成数据库文件 {dst}（源 {src}；最后一次替换失败：{last_err}；拷贝失败：{e}）"
        ) from e
    try:
        os.remove(src)
    except OSError:
        pass  # 源文件删不掉不影响使用（换个名字留着即可）
    logger.warning(f"os.replace 到 {dst} 失败（{last_err}），已改用覆盖拷贝方式生成")


def _prepare_database(config: ServerConfig, output_dir: str) -> str:
    """准备服务器要用的数据库：重建干净库 + 保存 initial.db 快照，返回工作库 final.db 路径。"""
    if config.db_path:
        db_file_path = config.db_path
        assert os.path.exists(db_file_path), f"Database file {db_file_path} not found"
        initial_db_path = os.path.join(output_dir, "initial.db")
        shutil.copy2(db_file_path, initial_db_path)
        logger.info(f"Saved initial database to {initial_db_path}")
    else:
        db_file_path = reset_single_database(
            input_db=config.db_schema_path,
            input_sample=config.sample_path,
            scenario=config.scenario,
            task_id=config.task_id,
            database_dir=output_dir,
        )
        final_db_path = os.path.join(output_dir, "final.db")
        if os.path.abspath(db_file_path) != os.path.abspath(final_db_path):
            _replace_or_copy(db_file_path, final_db_path)
        db_file_path = final_db_path
        logger.info(f"Created database at {db_file_path}")

        initial_db_path = os.path.join(output_dir, "initial.db")
        shutil.copy2(db_file_path, initial_db_path)
        logger.info(f"Saved initial database to {initial_db_path}")

    return db_file_path


# ============================================================
# 2. MCP 客户端 + XML 工具协议（替代 awm.core.agent）
# ============================================================

def get_system_prompt(max_steps: int = 10) -> str:
    """构造发给 LLM 的系统提示词：三个元工具 + 三标签协议。"""
    tools_str = dedent("""\
        1. list_tools
            - Description: List all available MCP tools in the current environment (name + one-line description only).
            - Arguments: None
            - Output: A list of tool names and their one-line descriptions

        2. get_tool_detail_of
            - Description: Get the detailed parameters (input schema) of ONE specific tool. You MUST call this BEFORE calling that tool for the first time, so you never guess its parameters.
            - Arguments:
                - tool_name: str, required, the name of the tool you want to inspect
            - Output: The full input schema (parameters, types, required/optional, descriptions) of that tool

        3. call_tool
            - Description: Call a MCP environment-specific tool
            - Arguments:
                - tool_name: str, required, the tool name in the list_tools output
                - arguments: str, required, the arguments for calling <tool_name>. You must pass a valid JSON string without any markdown fences or additional commentary. This JSON str will be parsed by the tool and executed. You can pass an empty JSON str if no arguments are required by <tool_name>.
            - Output: The result of the <tool_name> tool call""")

    return dedent(f"""\
        # MCP Tools

        You are at a MCP environment. You need to call MCP tools to assist with the user query. You have already logged in, and your user id is {CURRENT_USER_ID} if required for the MCP tool.

        You are provided with THREE functions within <tools></tools> XML tags:
        <tools>
        {tools_str}
        </tools>

        Working rules (IMPORTANT):
        - You MUST always call list_tools FIRST to discover the available tools, and only call it once.
        - Before calling a specific tool (call_tool) for the FIRST time, you MUST call get_tool_detail_of for that tool to learn its parameters. Do NOT guess the parameters.
        - You may call the same tool again later without re-querying its detail.

        In each step, you MUST output EXACTLY ONE of the following three tags:

        1. <tool_call>{{"name": <function-name>, "arguments": <args-json-object>}}</tool_call>
           → Call a tool (list_tools / get_tool_detail_of / call_tool).

        2. <thinking>...</thinking>
           → Brief reasoning WITHOUT calling any tool. The environment will reply "continue", and you proceed in the next step.

        3. <final_answer>...</final_answer>
           → Your final answer. Use this ONLY when you are confident the task has been FULLY completed.

        If the task only changes the database and requires no information to be returned, finish with:
        <final_answer>This task requires no return value. I have completed the task.</final_answer>

        Stopping rule (IMPORTANT):
        - You have at most {max_steps} steps to complete the task. If you cannot finish within {max_steps} steps, it will be counted as a failure, so work efficiently and avoid wasting steps.
        - <thinking> and <final_answer> also consume one step, so use <thinking> sparingly.

        Example #1 (list tools):
        <tool_call>
        {{"name": "list_tools", "arguments": null}}
        </tool_call>

        Example #2 (query tool detail):
        <tool_call>
        {{"name": "get_tool_detail_of", "arguments": {{"tool_name": "get_weather"}}}}
        </tool_call>

        Example #3 (call tool):
        <tool_call>
        {{"name": "call_tool", "arguments": {{"tool_name": "get_weather", "arguments": "{{\"city\": \"Beijing\"}}"}}}}
        </tool_call>""")


def _parse_dsml_tool_calls(content: str) -> list[dict]:
    """兜底：解析 DeepSeek/Qwen 原生 tool-calling 格式（DSML 标记 + final_tool_call）。

    模型偶尔不按 <tool_call> 协议输出，而是输出原生格式：
      1. DSML：<｜DSML｜ invoke name="call_tool"> ... <｜DSML｜ parameter name=...>v</｜DSML｜ parameter> ... </｜DSML｜ invoke>
      2. final_tool_call：<final_tool_call>{"name": ..., "arguments": ...}</final_tool_call>
    返回 [{"name", "arguments"}]（不含 id，由调用方统一补）。
    """
    V = r"[｜|]"  # 全角/半角竖线都认
    calls = []

    # ① DSML invoke / parameter
    invoke_re = re.compile(
        r"<" + V + r"?DSML" + V + r"?\s*invoke\b([^>]*)>(.*?)</" + V + r"?DSML" + V + r"?\s*invoke\s*>",
        re.DOTALL,
    )
    param_re = re.compile(
        r"<" + V + r"?DSML" + V + r"?\s*parameter\b([^>]*)>(.*?)</" + V + r"?DSML" + V + r"?\s*parameter\s*>",
        re.DOTALL,
    )

    def _attr(attrs, name):
        m = re.search(name + r'\s*=\s*["\']([^"\']*)["\']', attrs)
        return m.group(1) if m else ""

    for m in invoke_re.finditer(content):
        invoke_name = _attr(m.group(1), "name").strip()
        body = m.group(2)
        params = {}
        for pm in param_re.finditer(body):
            params[_attr(pm.group(1), "name").strip()] = pm.group(2).strip()

        if invoke_name == "call_tool":
            raw_args = params.get("arguments", "")
            parsed = tools_robust_json_loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else {}
            if isinstance(parsed, dict) and "tool_name" in parsed:
                calls.append({"name": "call_tool", "arguments": parsed})
            else:
                if not isinstance(parsed, dict):
                    parsed = {}
                calls.append({"name": "call_tool", "arguments": {"tool_name": params.get("tool_name", ""), "arguments": parsed}})
        elif invoke_name == "get_tool_detail_of":
            calls.append({"name": "get_tool_detail_of", "arguments": {"tool_name": params.get("tool_name", "")}})
        elif invoke_name == "list_tools":
            calls.append({"name": "list_tools", "arguments": None})
        elif invoke_name:
            arguments = {}
            for k, v in params.items():
                arguments[k] = tools_robust_json_loads(v) if isinstance(v, str) and v.strip().startswith("{") else v
            calls.append({"name": "call_tool", "arguments": {"tool_name": invoke_name, "arguments": arguments}})

    # ② final_tool_call 块（内容形如 {"name": ..., "arguments": ...}）
    if not calls:
        ftc_re = re.compile(r'<final_tool_call>\s*(.*?)\s*</final_tool_call>', re.DOTALL)
        for m in ftc_re.finditer(content):
            data = tools_robust_json_loads(m.group(1).strip())
            if not isinstance(data, dict):
                continue
            name = data.get("name", "")
            arguments = data.get("arguments", {})
            if not name:
                continue
            if name.startswith("mcp_tool_"):
                arguments = {"tool_name": name, "arguments": arguments if arguments else {}}
                name = "call_tool"
            calls.append({"name": name, "arguments": arguments})

    return calls


# DSML 标记里的竖线是「全角竖线」（U+FF5C），用 chr() 生成，避免源码里出现裸的特殊字符
_FULLWIDTH_BAR = chr(0xFF5C)

# DSML 外层标记的通用形状：可选闭合斜杠 + 1~2 个竖线 + DSML + 1~2 个竖线 + 其余属性
# （标签名可能被写成 calls / tool_calls / invoke / parameter，属性里还可能带 name="xxx"）
_DSML_TAG_RE = re.compile(
    r"</?\s*[" + _FULLWIDTH_BAR + r"|]{1,2}\s*DSML\s*[" + _FULLWIDTH_BAR + r"|]{1,2}[^>]*>"
)

# 约定协议的 tool_call 开/闭标签（拼接生成，避免源码里直接出现裸标签字面量）
_TOOL_CALL_OPEN = "<" + "tool_call" + ">"
_TOOL_CALL_CLOSE = "</" + "tool_call" + ">"

# 「原生 tool-calling 协议」的痕迹：模型没按约定输出 <tool_call>，而是直接吐自己预训练里的原生格式。
# DSML 是 DeepSeek 系的原生格式（<｜DSML｜invoke ...>），实测的 4 种坏法都带 "DSML" 字样；
# invoke/parameter/final_tool_call/function_call 是同一类原生化写法的其它常见形态。
_NATIVE_PROTOCOL_MARKERS = (
    "DSML",
    "<" + "invoke",
    "</" + "invoke",
    "<" + "parameter",
    "</" + "parameter",
    "<" + "final_tool_call" + ">",
    "<" + "function_call",
    "<" + "tool_calls" + ">",
)


def has_native_tool_protocol(text: str) -> bool:
    """判断这段 assistant 回复是否用了「非约定协议」（DSML / invoke / parameter / final_tool_call 等）。

    约定的工具调用协议只有一种：<tool_call>{"name": ..., "arguments": ...}</tool_call>。
    只要出现原生协议的痕迹，调用方就应该【驳回这次工具调用、不发给 env】，并要求模型按约定协议重发
    （见 lib/rollout_runner.py::FORMAT_CORRECTION_MESSAGE）——绝不能让坏格式静默变成
    「0 次工具调用」，也不能把模型的格式错误当成环境问题。
    """
    if not text:
        return False
    return any(marker in text for marker in _NATIVE_PROTOCOL_MARKERS)


def _scan_balanced_object(text: str, start: int) -> tuple[dict | None, int]:
    """从 text[start] == "{" 开始扫描一个花括号配平的对象，返回 (解析结果, 下一个扫描位置)。

    逐字符配平花括号，并跳过字符串字面量（含反斜杠转义），避免把字符串里的花括号算进配平；
    真正的 JSON 解析交给 tools_robust_json_loads（orjson → json → json_repair 逐级降级），
    因此模型把 JSON 写脏一点（多余逗号 / 少个引号）也能救回来。扫不到配平就返回 (None, start + 1)。
    """
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:                      # 字符串内部：只关心引号与转义
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:              # 配平成功 → 这一整段就是候选 JSON
                obj = tools_robust_json_loads(text[start:i + 1])
                return (obj if isinstance(obj, dict) else None), i + 1
    return None, start + 1


def _parse_loose_tool_calls(content: str) -> list[dict]:
    """兜底 2：解析「DSML 外层标记被写坏」的工具调用（实测样本见 output/env/_dsml_dump.txt）。

    本地 policy 模型输出原生 tool-calling 协议时，外层标记有 4 种实测坏法：
      1. 竖线重复：本该 1 个全角竖线，实际吐成 2 个；
      2. 标签名丢前缀：tool_calls 被写成 calls；
      3. 只有 parameter 的闭合标签、没有开标签，JSON 正文裸跟在 invoke 标签后面；
      4. tool_call 开标签写对了，闭合却错写成 DSML 那一串，于是开闭正则配不上。
    旧实现（_parse_dsml_tool_calls）只认「单个竖线 + 完整标签」，上述 4 种全部漏掉，
    结果工具调用被静默丢弃、整条轨迹以「没有任何工具调用」结束，env 阶段 rollout check 因此判不合格。

    做法：先把所有 DSML 外层标记整段抹掉（只留 JSON 正文），再从左往右扫出「带 name 字段的
    JSON 对象」当作工具调用。4 种坏法于是归一化成同一个「从文本里抠 JSON」的问题，
    不再依赖外层标记的确切写法。
    """
    stripped = _DSML_TAG_RE.sub(" ", content)          # 抹掉所有 DSML 外层标记，只留 JSON 正文
    stripped = stripped.replace(_TOOL_CALL_OPEN, " ")  # 半对的 tool_call 标签也一并抹掉
    stripped = stripped.replace(_TOOL_CALL_CLOSE, " ")

    calls: list[dict] = []
    idx = 0
    while True:
        start = stripped.find("{", idx)
        if start < 0:
            break
        data, idx = _scan_balanced_object(stripped, start)
        if data is None:               # 这个花括号不是 JSON 开头（比如正文里的解释文字），跳过
            continue
        name = str(data.get("name") or "").strip()
        if not name:                   # 没有 name 字段 → 不是工具调用
            continue
        arguments = data.get("arguments", {})
        if name.startswith("mcp_tool_"):
            arguments = {"tool_name": name, "arguments": arguments if arguments else {}}
            name = "call_tool"
        calls.append({"name": name, "arguments": arguments})

    return calls


def parse_tool_calls(content: str) -> list[dict]:
    """从模型回复文本里提取所有工具调用块。

    优先解析约定的 <tool_call> 标签；若没解析到任何工具，兜底解析 DeepSeek/Qwen 原生
    格式（DSML 标记 / final_tool_call），避免模型偶尔输出原生格式时工具调用被静默丢弃。
    """
    tool_calls = []
    # Use a project-specific plain-text tag for Qwen3: vLLM tokenizers may
    # consume the literal <tool_call> token and return an empty message.content.
    pattern = r'<(awm_tool_call|tool_call)>\s*(.*?)\s*</\1>'
    matches = [body for _, body in re.findall(pattern, content, re.DOTALL)]

    for i, match in enumerate(matches):
        data = tools_robust_json_loads(match.strip())
        if not data:
            logger.warning(f"Failed to parse tool call JSON: {match[:100]}")
            continue

        if isinstance(data, list):
            if len(data) > 0 and isinstance(data[0], dict):
                data = data[0]
            else:
                continue

        if not isinstance(data, dict):
            continue

        name = data.get("name", "")
        arguments = data.get("arguments", {})

        if name.startswith("mcp_tool_"):
            arguments = {
                "tool_name": name,
                "arguments": arguments if arguments else {},
            }
            name = "call_tool"

        tool_calls.append({
            "id": f"call_{int(time.time() * 1000)}_{i}",
            "name": name,
            "arguments": arguments,
        })

    if not tool_calls:
        # 兜底：模型偶尔不按 <tool_call> 协议输出 → 依次尝试「标准 DSML / final_tool_call」与「被写坏的 DSML」
        # 兜底 1：标准的 DSML（单竖线）/ final_tool_call 原生格式
        dsml_calls = _parse_dsml_tool_calls(content)
        # 兜底 2：DSML 外层标记被写坏（双竖线、丢 tool_ 前缀、缺 parameter 开标签、缺 </tool_call>）。
        # 仅在文本确实「像是想输出工具调用」时才启用，避免把 final_answer 里的普通 JSON 误判成工具调用。
        if not dsml_calls and ("DSML" in content or "<tool_call" in content):
            dsml_calls = _parse_loose_tool_calls(content)
        for dsml in dsml_calls:
            tool_calls.append({
                "id": f"call_{int(time.time() * 1000)}_{len(tool_calls)}",
                "name": dsml["name"],
                "arguments": dsml["arguments"],
            })

    return tool_calls


def parse_call_tool_arguments(arguments: dict | str | None) -> tuple[str, dict]:
    """把 call_tool 的外层参数解析成 (具体工具名, 具体工具参数)。"""
    if isinstance(arguments, str):
        arguments = tools_robust_json_loads(arguments)
    if not isinstance(arguments, dict):
        return "", {}

    tool_name = arguments.get("tool_name", "")
    inner_args = arguments.get("arguments", {})

    if tool_name.startswith("mcp_tool_"):
        tool_name = tool_name[len("mcp_tool_"):]

    if isinstance(inner_args, str):
        parsed = tools_robust_json_loads(inner_args) if inner_args.strip() else {}
        if isinstance(parsed, dict):
            inner_args = parsed
        else:
            inner_args = {}

    if not isinstance(inner_args, dict):
        inner_args = {}

    return tool_name, inner_args


def _format_input_schema(schema: dict, indent_level: int = 4, parent_required: list | None = None) -> str:
    """展示完整 JSON Schema，保留 nullable、嵌套数组、联合类型与所有约束。"""
    if not schema:
        return ""
    if parent_required is not None:
        schema = {**schema, "required": parent_required}
    return "\n".join(" " * indent_level + line for line in
                     json.dumps(schema, ensure_ascii=False, indent=2).splitlines()) + "\n"


def format_tools_for_response(tools: list[dict]) -> str:
    """把 MCP 工具列表格式化成「目录」（name + 一句话描述），不展开参数 schema。"""
    actual_tools = [t for t in tools if t.get('name') != 'list_tools']
    docs_text = f"Available MCP Tools ({len(actual_tools)} tools):\n"
    docs_text += "=" * 80 + "\n\n"
    for i, tool in enumerate(actual_tools, 1):
        name = tool.get("name", "")
        description = tool.get("description", "")
        mcp_name = name
        first_line = description.split('\n')[0].strip() if description else "No description"
        docs_text += f"{i}. {mcp_name}\n"
        docs_text += f"   Description: {first_line}\n\n"
    return docs_text.strip()


def format_tool_detail(tool: dict) -> str:
    """把单个工具格式化成完整详情（含参数 schema）。"""
    name = tool.get("name", "")
    description = tool.get("description", "")
    input_schema = tool.get("inputSchema", tool.get("input_schema", {}))
    mcp_name = name
    text = f"Tool: {mcp_name}\n"
    text += f"Description: {description}\n"
    if input_schema and input_schema != {"type": "object", "properties": {}}:
        text += "Parameters:\n"
        text += _format_input_schema(input_schema)
    else:
        text += "Parameters: None\n"
    return text.strip()


def find_tool_by_name(tools: list, tool_name: str):
    """在工具列表里按名字找单个工具（兼容 mcp_tool_ 前缀）。"""
    if not tools:
        return None
    target = tool_name[len("mcp_tool_"):] if tool_name.startswith("mcp_tool_") else tool_name
    for t in tools:
        if t.get("name") == target:
            return t
    return None


class MCPToolExecutor:
    """用 mcp 官方客户端封装连接，提供 list_tools() 和 call_tool() 两个异步方法。"""

    def __init__(self, mcp_url: str, timeout: float = 60.0):
        self.mcp_url = mcp_url
        self.timeout = timeout
        self._tools: list[dict] = []
        self._http_cm = None
        self._session: ClientSession | None = None

    async def _connect(self) -> None:
        if self._session is not None:
            return
        self._http_cm = streamablehttp_client(self.mcp_url, timeout=self.timeout)
        read, write, _ = await self._http_cm.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await asyncio.wait_for(self._session.initialize(), timeout=self.timeout)

    async def list_tools(self) -> list[dict]:
        await self._connect()
        result = await asyncio.wait_for(self._session.list_tools(), timeout=self.timeout)
        self._tools = []
        for t in result.tools:
            self._tools.append({
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema or {},
            })
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        await self._connect()
        result = await asyncio.wait_for(
            self._session.call_tool(tool_name, arguments),
            timeout=self.timeout,
        )
        parts = []
        for c in result.content:
            if hasattr(c, 'text'):
                parts.append(c.text)
            else:
                parts.append(str(c))
        text = "\n".join(parts)
        if result.isError:
            return f"Error: {text}"
        return text

    async def close(self) -> None:
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        if self._http_cm is not None:
            try:
                await self._http_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._http_cm = None


# ============================================================
# 3. 验证器（替代 awm.core.verify）
# ============================================================

class VerificationMode(Enum):
    sql = "sql"    # 代码增强验证：返回信息字典，交由 LLM 裁判
    code = "code"  # 纯代码验证：直接返回 complete / others


@dataclass
class VerifyConfig:
    input: str
    init_db_path: str | None = None
    final_db_path: str | None = None
    mode: VerificationMode = VerificationMode.sql
    verifier_path: str | None = None
    verifier_code_path: str | None = None

    def pre_process(self):
        if self.mode == VerificationMode.sql:
            provider = os.environ.get("AWM_SYN_LLM_PROVIDER", "").lower()
            if provider == "azure":
                missing = []
                if not os.environ.get("AZURE_ENDPOINT_URL"):
                    missing.append("AZURE_ENDPOINT_URL")
                if not os.environ.get("AZURE_OPENAI_API_KEY"):
                    missing.append("AZURE_OPENAI_API_KEY")
                if missing:
                    raise ValueError("SQL verification mode requires Azure LLM config. Missing env vars.")
            else:
                if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("OPENAI_BASE_URL"):
                    raise ValueError("SQL verification mode requires LLM config. Missing env vars.")
            if not os.environ.get("AWM_SYN_OVERRIDE_MODEL"):
                raise ValueError("SQL verification mode requires AWM_SYN_OVERRIDE_MODEL to be set.")


def _call_verifier_func(verify_func, initial_db_path, final_db_path, final_answer=None):
    """按验证函数的参数签名智能传参并调用。"""
    sig = inspect.signature(verify_func)
    params = sig.parameters

    kwargs = {}
    if "initial_db_path" in params:
        kwargs["initial_db_path"] = initial_db_path
    if "final_db_path" in params:
        kwargs["final_db_path"] = final_db_path
    if "final_answer" in params:
        kwargs["final_answer"] = final_answer or ""

    if not kwargs and len(params) >= 2:
        args = [initial_db_path, final_db_path]
        if len(params) >= 3:
            args.append(final_answer or "")
        return verify_func(*args)

    return verify_func(**kwargs)


def execute_sql_verifier(verifier_code, function_name, initial_db_path, final_db_path):
    """执行 sql 模式的验证代码：对比初始与最终数据库状态。"""
    if not os.path.exists(initial_db_path):
        return {"execution_status": "error", "error_message": f"Initial DB not found: {initial_db_path}"}
    if not os.path.exists(final_db_path):
        return {"execution_status": "error", "error_message": f"Final DB not found: {final_db_path}"}

    original_modes = {}
    for path in [initial_db_path, final_db_path]:
        try:
            original_modes[path] = os.stat(path).st_mode
            os.chmod(path, 0o444)
        except Exception:
            pass

    try:
        namespace = {"sqlite3": sqlite3, "json": json, "os": os, "__builtins__": __builtins__}
        exec(verifier_code, namespace)
        verify_func = namespace.get(function_name)
        if not verify_func:
            return {"execution_status": "error", "error_message": f"Function '{function_name}' not found"}
        result = _call_verifier_func(verify_func, initial_db_path, final_db_path)
        try:
            json.dumps(result)
        except TypeError:
            result = sanitize_for_json(result)
        return result
    except Exception as e:
        return {"execution_status": "error", "error_message": f"Execution error: {e}"}
    finally:
        for path, mode in original_modes.items():
            try:
                os.chmod(path, mode)
            except Exception:
                pass


def execute_code_verifier(verifier_code, function_name, initial_db_path, final_db_path, final_answer=None):
    """执行 code 模式的验证代码：确定性地返回 complete / others。"""
    if not os.path.exists(initial_db_path):
        return {"result": "others", "execution_status": "error", "error_message": f"Initial DB not found: {initial_db_path}"}
    if not os.path.exists(final_db_path):
        return {"result": "others", "execution_status": "error", "error_message": f"Final DB not found: {final_db_path}"}

    original_modes = {}
    for path in [initial_db_path, final_db_path]:
        try:
            original_modes[path] = os.stat(path).st_mode
            os.chmod(path, 0o444)
        except Exception:
            pass

    try:
        namespace = {"sqlite3": sqlite3, "json": json, "os": os, "re": re, "__builtins__": __builtins__}
        exec(verifier_code, namespace)
        verify_func = namespace.get(function_name)
        if not verify_func:
            return {"result": "others", "execution_status": "error", "error_message": f"Function '{function_name}' not found"}
        result = _call_verifier_func(verify_func, initial_db_path, final_db_path, final_answer)
        if not isinstance(result, dict) or "result" not in result:
            return {"result": "others", "execution_status": "error", "error_message": f"Invalid return format: {type(result).__name__}"}
        result_value = result.get("result", "others")
        if result_value not in ("complete", "others"):
            result_value = "others"
        return {"result": result_value, "execution_status": "success", "raw_result": result}
    except Exception as e:
        return {"result": "others", "execution_status": "error", "error_message": f"Execution error: {e}"}
    finally:
        for path, mode in original_modes.items():
            try:
                os.chmod(path, mode)
            except Exception:
                pass




def run_verifier(verifier_entry, verifier_mode, initial_db_path, final_db_path, final_answer=None):
    """运行对应模式的验证器，返回 (reward_type, details)。"""
    verification = verifier_entry.get("verification", {})
    code = verification.get("code", "")

    if not code or not isinstance(code, str) or len(code.strip()) < 10:
        return "judge_error", {"error": "No valid verifier code found"}

    default_func = "verify_task_completion" if verifier_mode == "code" else "verify_task"
    func_name = default_func
    for line in code.split("\n"):
        line = line.strip()
        if line.startswith("def verify_") and "(" in line:
            func_name = line.split("(")[0].replace("def ", "").strip()
            break

    if verifier_mode == "code":
        result = execute_code_verifier(code, func_name, initial_db_path, final_db_path, final_answer)
        if result.get("execution_status") == "error":
            return "judge_error", result
        return result.get("result", "others"), result

    result = execute_sql_verifier(code, func_name, initial_db_path, final_db_path)
    if isinstance(result, dict) and result.get("execution_status") == "error":
        return "judge_error", result
    return "incomplete", result


async def run_verify(config: VerifyConfig):
    """对 agent 运行目录执行验证，返回含 reward_type 的结果字典。"""
    run_dir = config.input
    mode = config.mode
    if isinstance(mode, VerificationMode):
        mode = mode.value
    if mode not in ("sql", "code"):
        raise ValueError(f"Invalid mode: {mode}. Must be 'sql' or 'code'.")

    trajectory_path = os.path.join(run_dir, "trajectory.json")
    if not os.path.exists(trajectory_path):
        raise FileNotFoundError(f"trajectory.json not found in {run_dir}")

    with open(trajectory_path, encoding="utf-8") as f:
        trajectory_data = json.load(f)

    scenario = trajectory_data.get("scenario")
    task_id = trajectory_data.get("task_id")
    task = trajectory_data.get("task", "")
    messages = trajectory_data.get("messages", [])

    if scenario is None or task_id is None:
        raise ValueError("trajectory.json must contain 'scenario' and 'task_id' fields")

    initial_db_path = config.init_db_path or os.path.join(run_dir, "initial.db")
    final_db_path = config.final_db_path or os.path.join(run_dir, "final.db")

    if not os.path.exists(initial_db_path):
        raise FileNotFoundError(f"Initial database not found: {initial_db_path}")
    if not os.path.exists(final_db_path):
        raise FileNotFoundError(f"Final database not found: {final_db_path}")

    if mode == "code":
        verifier_file = config.verifier_code_path or "./outputs/gen_verifier.pure_code.jsonl"
    else:
        verifier_file = config.verifier_path or "./outputs/gen_verifier.jsonl"

    if not os.path.exists(verifier_file):
        raise FileNotFoundError(f"Verifier file not found: {verifier_file}")

    verifier_data = tools_jsonl_load(verifier_file)
    verifier_entry = find_scenario_entry(verifier_data, scenario, task_idx=task_id)
    if not verifier_entry:
        raise ValueError(f"No verifier found for scenario={scenario}, task_id={task_id} in {verifier_file}")

    logger.info(f"Running {mode} verification for scenario={scenario}, task_id={task_id}")

    final_answer = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            final_answer = msg.get("content", "")
            break

    reward_type, verify_result = run_verifier(
        verifier_entry, mode, initial_db_path, final_db_path, final_answer
    )

    logger.info(f"Verifier result: reward_type={reward_type}")

    output = {
        "scenario": scenario,
        "task_id": task_id,
        "task": task,
        "mode": mode,
        "reward_type": reward_type,
        "verify_result": verify_result,
    }

    output_path = os.path.join(run_dir, f"verify.{mode}.json")
    tools_json_save(output, output_path)
    logger.info(f"Saved verification result to {output_path}")

    return output

