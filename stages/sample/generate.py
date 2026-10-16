"""
多 agent 版本的示例数据生成（改造 awm/core/sample.py，流水线第 4 步）。

流程：
  ① DataGenerator 从零生成全库（只在最开始用一次）
  → ② 规则校验循环（幻觉表/缺失表/违规表 → fixer 修到通过）
  → ③ 语义评审循环（评审不过的表 → fixer 修到通过）
  → ④ 执行循环（INSERT 报错 → fixer 修到零错误）

成功标准：failed == 0（100% 零错误）才保留；否则重试；重试耗尽放弃该场景（不写入
gen_sample_ma.jsonl），并清空该场景 .db 的数据（保留第三阶段建好的表结构，不删库）。

运行方式（在项目根目录 ma_upgrade 下）：
python -m stages.sample.generate
"""

import json     # 把评审意见/执行错误序列化成 JSON 字符串
import os       # 判断文件是否存在、创建目录、删除 .db 文件
import sqlite3  # 执行 INSERT 语句（真正插入数据）
import sys      # 把项目根目录加进 sys.path
from dataclasses import dataclass   # 用数据类定义配置项
from pathlib import Path            # 处理文件路径

# 确保项目根目录在 sys.path 上，便于 import awm / ma_upgrade
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent   # 上溯三级到项目根目录
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 默认输出/建库目录用「绝对路径」，与 CWD 无关
_DEFAULT_TASK= str(_PROJECT_ROOT / "output" / "task"/"gen_tasks_ma.jsonl")
_DEFAULT_DB=str(_PROJECT_ROOT / "output" / "db"/"gen_db_ma.jsonl")
_DEFAULT_OUTPUT = str(_PROJECT_ROOT / "output" / "sample" / "gen_sample_ma.jsonl")
_DEFAULT_DB_DIR = str(_PROJECT_ROOT / "output" / "db" / "databases")
_DEFAULT_BAD_OUTPUT = str(_PROJECT_ROOT / "output" / "sample" / "sample_bad_tasks.jsonl")

from loguru import logger   # 日志工具

from lib.interrupt import run_cli, check_cancelled
from lib.interrupt import InterruptibleThreadPoolExecutor as ThreadPoolExecutor, as_completed
from stages.db.db_utils import create_sqlite_database   # 复用原项目的建库函数（重置数据库为纯表结构）
from lib.gpt import GPTClient   # 大模型客户端（给 data_generator/fixer 配更长超时）
from lib.tools import (                          # 项目自带的实用小工具
    tools_jsonl_load,
    normalize_scenario_name, format_db_schema
)

from lib.db_diff import snapshot_db, snapshot_size
from lib.orchestrator import Agent   # 通用 agent 原语
from stages.sample.prompts import (   # 本阶段的提示词
    DATA_GENERATION_SYSTEM_PROMPT,
    ALL_TABLES_GENERATION_USER_PROMPT,
    DATA_REVIEW_SYSTEM_PROMPT,
    DATA_FIX_SYSTEM_PROMPT,
)
from stages.sample.validators import validate_sample   # 本阶段的规则校验
from tqdm import tqdm   # 进度条


@dataclass
class Config:
    """示例数据生成的配置项。"""
    input_task: str =_DEFAULT_TASK                           # 任务文件（gen_tasks_ma.jsonl）
    input_db: str =_DEFAULT_DB                             # 表结构文件（gen_db_ma.jsonl）
    output: str = _DEFAULT_OUTPUT              # 输出文件（默认绝对路径）
    bad_output: str = _DEFAULT_BAD_OUTPUT      # 坏任务清单（每次跑完记录失败的任务）
    database_dir: str = _DEFAULT_DB_DIR        # .db 文件存放目录
    model: str = "your-llm-model-name"         # 模型名（占位，必须被覆盖）
    allowed_scenarios: list[str] | None = None  # 只处理这些场景
    limit: int | None = None                   # 只处理前 limit 个场景
    max_retry: int = 2                         # 生成+评审+修复循环最大轮次
    max_concurrency: int = 4                   # 场景级并发数（降低并发以减少连接压力/Connection error）
    resume: bool = True                       # 是否断点续跑（跳过已完成的场景）
    max_tokens=50000
    max_snapshot_chars: int = 10 * 1024

    def pre_process(self):
        """开工前检查：补齐默认值、校验输入、解析模型名。"""
        if self.limit is None:
            self.limit = int(1e18)
        assert self.input_task and os.path.exists(self.input_task), "input_task is required"
        assert self.input_db and os.path.exists(self.input_db), "input_db is required"
        assert self.output.endswith(".jsonl"), "Output path .jsonl is required"
        if os.environ.get("AWM_SYN_OVERRIDE_MODEL"):
            self.model = os.environ.get("AWM_SYN_OVERRIDE_MODEL")
        assert self.model != "your-llm-model-name", "请设置模型名（--model 或 AWM_SYN_OVERRIDE_MODEL）"



def build_agents(config: Config) -> dict:
    """构建 3 个 agent：数据生成 / 数据评审 / 修复。"""
    return {
        "data_generator": Agent(
            "data_generator", DATA_GENERATION_SYSTEM_PROMPT,
            client=GPTClient(timeout=1200),   # 一次性生成全库输出长，默认 120 秒不够，给足超时
            model=config.model, temperature=0.7, max_tokens=config.max_tokens,
        ),
        "data_reviewer": Agent(
            "data_reviewer", DATA_REVIEW_SYSTEM_PROMPT,
            client=GPTClient(timeout=1200),   # 评审输入含全量 sample_data，给足超时
            model=config.model, temperature=0.0, max_tokens=config.max_tokens,
        ),

        "fixer": Agent(
            "fixer", DATA_FIX_SYSTEM_PROMPT,
            client=GPTClient(timeout=1200),   # 修复输出也较长，给足超时
            model=config.model, temperature=0.0, max_tokens=config.max_tokens,
        ),
    }


def _build_all_tables_gen_input(scenario_name: str, tasks: list, db_schema: dict) -> str:
    """为整个场景拼装一次性生成提示词（全量 schema + 任务，表与表一起生成，保证外键一致）。"""
    tasks_str = "\n".join(f"- {t}" for t in tasks)
    schema_str = format_db_schema(db_schema)
    return ALL_TABLES_GENERATION_USER_PROMPT.format(
        scenario_name=scenario_name,
        tasks_list=tasks_str,
        database_schema=schema_str,
    )


def _clean_tables(tables: list) -> list:
    """规整 LLM 返回的表列表：过滤空 INSERT、脏数据，返回 [{table_name, insert_statements}]。"""
    out = []
    for t in tables:
        stmts = t.get("insert_statements", []) if isinstance(t, dict) else []
        stmts = [s for s in stmts if isinstance(s, str) and s.strip()]
        if stmts:
            out.append({"table_name": t.get("table_name", "unknown"), "insert_statements": stmts})
    return out


def _generate_all_tables(scenario_name: str, tasks: list, db_schema: dict, agents: dict) -> list:
    """一次性生成整个场景所有表的 INSERT（只在冷启动时用一次），返回 [{table_name, insert_statements}]。"""
    prompt = _build_all_tables_gen_input(scenario_name, tasks, db_schema)  # 拼装全量 schema+任务的提示词
    try:
        r = agents["data_generator"].run_json(prompt)  # 让 LLM 一次性生成所有表，返回 JSON
    except Exception as e:
        logger.warning(f"{scenario_name}: 数据生成失败（{e}）")
        return []
    if isinstance(r, dict):
        tables = r.get("tables", [])
    elif isinstance(r, list):
        tables = r  # 模型偶发输出数组 [{"table_name", "insert_statements"}, ...]
    else:
        tables = []
    return _clean_tables(tables)


def _topo_sort_table_names(db_schema: dict) -> list:
    """按外键依赖拓扑排序表名（父表在前、子表在后）。解析 DDL 里的 REFERENCES 提取外键关系。"""
    import re
    deps = {}   # 表名 -> 它的父表集合（被外键引用的表）
    order = []  # schema 里的表名原始顺序（保证遍历顺序稳定）
    for t in db_schema.get("tables", []):  # 遍历每张表
        name = (t.get("name") or "").lower()  # 表名统一小写（DDL 里外键引用也是小写比较）
        if not name:
            continue
        order.append(name)  # 记录原始顺序
        ddl = (t.get("ddl") or "").upper()  # DDL 转大写，便于正则匹配 REFERENCES
        parents = set()
        for m in re.finditer(r"REFERENCES\s+(\w+)", ddl):  # 从 DDL 里找出所有被引用的父表
            parents.add(m.group(1).lower())
        deps[name] = parents  # 记录该表的父表集合
    ordered = []   # 拓扑排序结果
    visited = set()  # 已访问（避免环导致无限递归）
    def visit(n):
        """DFS：先访问父表，再把自己追加到结果（父表在前）。"""
        if n in visited:
            return
        visited.add(n)
        for p in deps.get(n, set()):  # 先递归访问所有父表
            if p in deps:
                visit(p)
        ordered.append(n)  # 父表都排完后，自己再进结果
    for n in order:  # 按原始顺序逐个 visit（保证未被引用的表也排进去）
        visit(n)
    return ordered


def _order_tables_by_fk(sample_data: dict, db_schema: dict) -> dict:
    """把 sample_data 的表按外键依赖顺序重排（父表在前）。返回重排后的 sample_data。"""
    topo = _topo_sort_table_names(db_schema)  # 得到「父表在前」的表名顺序
    by_name = {t.get("table_name", "").lower(): t for t in sample_data.get("tables", [])}  # 表名 -> 表对象
    ordered_tables = []  # 按拓扑顺序排好的表
    for name in topo:  # 按父表→子表的顺序取表
        if name in by_name:
            ordered_tables.append(by_name[name])
    seen = {t.get("table_name", "").lower() for t in ordered_tables}  # 已排好的表名（去重判断用）
    for t in sample_data.get("tables", []):  # 兜底：把拓扑排序没覆盖的表（如无外键的表）补到末尾
        if t.get("table_name", "").lower() not in seen:
            ordered_tables.append(t)
    return {"tables": ordered_tables}

def _review(sample_data: dict, scenario_name: str, tasks: list, db_schema: dict, agents: dict) -> list:
    """③ 语义评审：逐表 verdicts。返回 [{"table", "pass", "issue"}]。"""
    payload = {"scenario": scenario_name, "user_tasks": tasks, "db_schema": db_schema, "sample_data": sample_data}  # 打包给评审者
    try:
        r = agents["data_reviewer"].run_json(payload)  # 评审者逐表给 pass/issue
    except Exception as e:
        logger.warning(f"{scenario_name}: 数据评审失败（{e}），视为无意见")
        return []
    return r.get("results", []) if isinstance(r, dict) else []  # 防御：解析失败返回空（视为无意见）


def _fix_tables(scenario_name: str, tasks: list, db_schema: dict, sample_data: dict, problems: str, agents: dict) -> dict:
    """fixer 基于 problems 定向修复（只返回修改/新增/删除的表），好表原样保留。返回修复后的完整 sample_data。"""
    payload = {
        "scenario": scenario_name,
        "user_tasks": tasks,
        "db_schema": db_schema,
        "sample_data": sample_data,
        "problems": problems,
    }
    try:
        r = agents["fixer"].run_json(payload)
    except Exception as e:
        logger.warning(f"{scenario_name}: fixer 修复失败（{e}），原样返回")
        return sample_data   # fixer 失败则原样返回，不破坏原数据
    if not isinstance(r, dict) or not r.get("tables"):
        return sample_data   # fixer 失败则原样返回，不破坏原数据
    fixed_by_name = {}
    for t in r.get("tables", []):
        if not isinstance(t, dict):
            continue
        name = t.get("table_name")
        stmts = t.get("insert_statements", [])
        if not isinstance(name, str) or not name:   # 表名非法（None/数字）则跳过该表
            continue
        if not isinstance(stmts, list):              # insert_statements 非 list 则视为空（触发删除该表）
            stmts = []
        stmts = [s for s in stmts if isinstance(s, str)]  # 只保留字符串 INSERT，过滤掉 float 等噪声
        fixed_by_name[name] = stmts
    # 删除 = 「fixer 返回空 insert_statements 且该表不在 db_schema 里（幻觉表）」；
    # 若表在 db_schema 里但返回空（任务要创建的空表），则保留空 entry（不删除）。
    schema_names = {t.get("name") for t in db_schema.get("tables", [])}
    tables_to_remove = {name for name, stmts in fixed_by_name.items() if not stmts and name not in schema_names}
    merged = []
    for t in sample_data.get("tables", []):
        name = t.get("table_name")
        if name in tables_to_remove:
            continue   # 幻觉表：fixer 要求删除
        if name in fixed_by_name:
            merged.append({"table_name": name, "insert_statements": fixed_by_name[name]})  # 修复版
        else:
            merged.append(t)   # 好表原样保留
    merged_names = {t.get("table_name") for t in merged}
    for name, stmts in fixed_by_name.items():  # 追加 fixer 新增的表（缺失表）
        if name not in merged_names and stmts:
            merged.append({"table_name": name, "insert_statements": stmts})
    return {"tables": merged}


def execute_inserts(db_path: str, sample_data: dict, db_schema: dict | None = None) -> tuple[int, int, set, set]:
    """真正执行 INSERT：返回 (成功数, 失败数, 错误集合, 失败的表名集合)。

    若传入 db_schema，则先按外键依赖拓扑排序（父表先插、子表后插），避免外键顺序错误。
    """
    if not os.path.exists(db_path):
        return 0, 0, {"Database file not found"}, set()   # 库不存在直接返回失败标记
    if db_schema is not None:
        sample_data = _order_tables_by_fk(sample_data, db_schema)   # 按父表→子表重排，避免外键顺序错误
    conn = sqlite3.connect(db_path)   # 打开连接（复用第三阶段建好的库）
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")   # 开启外键约束检查：外键引用不存在的父表主键会报错
    successful = 0   # 成功插入条数
    failed = 0       # 失败插入条数
    errors = set()        # 错误信息集合（去重，供 fixer 看）
    failed_tables = set() # 有失败 INSERT 的表名集合（供 fixer 定位要修哪些表）
    for table_data in sample_data.get("tables", []):  # 逐表
        table_name = table_data.get("table_name", "unknown")
        for stmt in table_data.get("insert_statements", []):  # 逐条 INSERT
            try:
                cursor.execute(stmt)   # 执行这条 INSERT
                successful += 1
            except Exception as e:  # 单条失败不中断整体，记录后继续（最后统一判断 failed 是否为零）
                failed += 1
                failed_tables.add(table_name)
                msg = f"表 {table_name} 的语句 {str(stmt)[:200]} 报错: {str(e)[:300]}"  # 截断，避免超长刷屏
                errors.add(msg)
                logger.error(f"  ✗ INSERT 失败：{msg}")   # 失败原因实时打印，便于排查
    conn.commit()   # 提交所有成功执行的 INSERT
    conn.close()
    return successful, failed, errors, failed_tables


def _clear_tables(db_path: str):
    """清空数据库所有表的数据（保留表结构）。用 sqlite_master 遍历真实存在的表。"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    for (name,) in cursor.fetchall():
        cursor.execute(f'DELETE FROM "{name}";')   # foreign_keys 默认 OFF，删除顺序无关
    conn.commit()
    conn.close()


def _ensure_empty_db(scenario_name: str, db_schema: dict, database_dir: str) -> str:
    """确保库存在且为空：存在则清空数据（保留表结构），不存在才建库。返回 db_path。"""
    db_path = os.path.join(database_dir, f"{normalize_scenario_name(scenario_name)}.db")
    if os.path.exists(db_path):
        _clear_tables(db_path)   # 清数据（快，不重跑 DDL）
    else:
        db_path, _, _, _ = create_sqlite_database(scenario_name, db_schema, database_dir)   # 兜底建库
    return db_path


def _reset_and_execute_task(db_path: str, sample_data: dict, db_schema: dict) -> tuple[int, int, set, set]:
    """清空任务级 db 的数据（保留表结构）→ 按外键顺序逐条执行 INSERT → 返回 (成功数, 失败数, 错误集合, 失败表名集合)。"""
    _clear_tables(db_path)  # 清空任务级 db 的数据（db 已由 db 阶段建好，这里只清数据）
    return execute_inserts(db_path, sample_data, db_schema)  # 真正执行 INSERT


def _clear_db_data_task(scenario_name: str, task_id: str, db_path: str):
    """放弃任务时：清空任务级 .db 的数据（保留表结构），不删除 db 阶段建好的库。"""
    if db_path and os.path.exists(db_path):
        _clear_tables(db_path)  # 只清数据，保留表结构（后续重试或下游还能用）
        logger.warning(f"{scenario_name}/{task_id}: 已清空 {db_path} 的数据（保留表结构，不删库）")


def _build_result(scenario_name: str, task_id: str, task: str, db_path: str, sample_data: dict,
                  initial_snapshot: dict, initial_snapshot_len: int) -> dict:
    """把 sample_data 打包成结果（统计表数 + INSERT 条数）。"""
    tables = sample_data.get("tables", [])
    total_tables = len(tables)  # 表数
    total_inserts = sum(len(t.get("insert_statements", [])) for t in tables)  # INSERT 总条数
    return {
        "scenario": scenario_name,
        "task_id": task_id,
        "task": task,
        "db_path": db_path,
        "tables_count": total_tables,
        "inserts_count": total_inserts,
        "sample_data": sample_data,
        "initial_snapshot": initial_snapshot,
        "initial_snapshot_len": initial_snapshot_len,
    }


def generate_sample_for_task(task_item: dict, db_item: dict, config: Config):
    """对单个场景：generator 从零生成 → 规则校验/语义评审/执行报错都收集成 problems → fixer 修复，循环到零错误。"""
    # 每个线程独立构建 agents（独立 GPTClient，避免跨线程共享 httpx 连接池导致挂起）
    agents = build_agents(config)
    scenario_name = task_item["scenario"]
    task_id = task_item["task_id"]
    task = task_item["task"]
    tasks = [task]   # 单任务列表（任务级：只为这一个任务设计初始状态）
    db_schema = db_item.get("db_schema", {})
    db_path = db_item.get("db_path", "")   # 任务级 db 路径（db 阶段已建好，这里只清数据 + 执行 INSERT）
    if db_path and not os.path.isabs(db_path):
        db_path = os.path.join(_PROJECT_ROOT, db_path)   # 相对路径 → 还原为绝对路径

    sample_data = None
    problems = ""   # 本轮收集的问题（规则校验 + 语义评审 + 执行报错），下一轮交给 fixer

    for attempt in range(config.max_retry + 1):
        check_cancelled()
        # 第一轮（或上一轮结果为空）→ generator 从零生成；否则 → fixer 基于上一轮 + 问题修复
        prev_tables = sample_data.get("tables", []) if isinstance(sample_data, dict) else []
        if attempt == 0 or not prev_tables:
            sample_data = {"tables": _generate_all_tables(scenario_name, tasks, db_schema, agents)}
            logger.info(f"{scenario_name}: generator 从零生成 {len(sample_data['tables'])} 张表")
        else:
            sample_data = _fix_tables(scenario_name, tasks, db_schema, sample_data, problems, agents)
            logger.info(f"{scenario_name}: 第 {attempt + 1} 轮 fixer 修复")

        tables = sample_data.get("tables", []) if isinstance(sample_data, dict) else []
        if not tables:
            logger.warning(f"{scenario_name}: 生成结果为空（第 {attempt + 1} 轮），交给 generator 重新生成")
            continue

        # ① 规则校验（确定性）→ 收集问题
        _, rule_violations = validate_sample(sample_data)
        rule_problems = [f"表「{violation['name']}」: {'; '.join(violation['reasons'])}" for violation in rule_violations]

        # ② 语义评审 → 收集问题
        verdicts = _review(sample_data, scenario_name, tasks, db_schema, agents)
        review_problems = [f"表「{verdict.get('table')}」: {verdict.get('issue', '未说明')}" for verdict in verdicts if not verdict.get("pass")]

        problems = "\n".join(rule_problems + review_problems)
        if problems:
            logger.warning(f"{scenario_name}: 规则/语义校验发现问题（第 {attempt + 1} 轮）：{problems}")
            continue

        # ③ 真正执行（清库 + 按外键顺序逐条执行 INSERT）
        successful, failed, errors, _ = _reset_and_execute_task(db_path, sample_data, db_schema)
        if failed == 0:
            try:
                initial_snapshot = snapshot_db(db_path)
                initial_snapshot_len = snapshot_size(initial_snapshot)
            except Exception as e:
                _clear_db_data_task(scenario_name, task_id, db_path)
                return {
                    "scenario": scenario_name,
                    "task_id": task_id,
                    "task": task,
                    "reason": f"initial snapshot failed: {type(e).__name__}: {e}",
                }
            if initial_snapshot_len > config.max_snapshot_chars:
                _clear_db_data_task(scenario_name, task_id, db_path)
                return {
                    "scenario": scenario_name,
                    "task_id": task_id,
                    "task": task,
                    "reason": (
                        f"initial snapshot too large: {initial_snapshot_len} chars "
                        f"> configured limit {config.max_snapshot_chars}"
                    ),
                    "initial_snapshot_len": initial_snapshot_len,
                    "max_snapshot_chars": config.max_snapshot_chars,
                }
            logger.success(f"{scenario_name}: {successful} 条 INSERT 全部执行成功")
            # 产物按外键拓扑排序（父表在前），保证下游（rl 等）按原始顺序执行也不会因外键顺序失败
            sample_data = _order_tables_by_fk(sample_data, db_schema)
            return _build_result(
                scenario_name, task_id, task, db_path, sample_data,
                initial_snapshot, initial_snapshot_len,
            )

        # ④ 执行报错 → 交给 fixer（下一轮修复）
        problems = "【执行 INSERT 报错】\n" + "\n".join(sorted(errors))
        logger.warning(f"{scenario_name}: 执行报错（第 {attempt + 1} 轮），交给 fixer 修复")

    # 重试耗尽 → 放弃该任务，清空 .db 数据（保留表结构），返回失败记录
    _clear_db_data_task(scenario_name, task_id, db_path)
    logger.error(f"{scenario_name}/{task_id}: 重试 {config.max_retry + 1} 次后仍未通过，放弃该任务")
    return {"scenario": scenario_name, "task_id": task_id, "task": task, "reason": problems or "重试耗尽未通过"}


def _task_sort_key(item: dict):
    """排序键：(scenario 字典序, task 序号自然序)。task_2 应排在 task_10 之前。"""
    scenario = item.get("scenario", "")
    task_id = item.get("task_id", "")
    try:
        n = int(task_id.split("_", 1)[1])
    except (ValueError, IndexError):
        n = 0
    return (scenario, n)


def _append_result(result: dict, output_path: str):
    """把单个场景结果追加写一行到 output（JSONL 追加模式，中断/断网不丢已完成的）。"""
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def run(config: Config):
    """入口：读取任务+表结构 → 展开成任务级 item → 断点续跑过滤 → 并发（进度条）生成并插入数据 → 每完成一个任务增量落盘。"""
    logger.info("多 agent 示例数据生成开始 ...")
    tasks_data = tools_jsonl_load(config.input_task)  # 读任务文件（每场景的任务列表）
    db_data = tools_jsonl_load(config.input_db)        # 读表结构文件（任务级）
    db_map = {(d["scenario"], d.get("task_id", "")): d for d in db_data}  # (场景名, task_id) -> 表结构

    # 展开成任务级 item：每个任务独立生成一份 sample（只为这一个任务的初始状态）
    items = []
    for t in tasks_data:
        scenario_name = t["scenario"]
        for i, task in enumerate(t.get("tasks", []), start=1):
            task_id = f"task_{i}"
            key = (scenario_name, task_id)
            if key not in db_map:
                logger.warning(f"{scenario_name}/{task_id}: 该任务在db没有成功生成db_schema和数据库结构，跳过")
                continue
            items.append({"scenario": scenario_name, "task_id": task_id, "task": task, "db_item": db_map[key]})

    # 按 (scenario, task 自然序) 排序，保证后续取到的是确定性的顺序
    items.sort(key=_task_sort_key)

    # 准备输出目录
    out_dir = os.path.dirname(config.output) or "."
    os.makedirs(out_dir, exist_ok=True)

    # 每次跑完重新生成 bad_tasks 清单（覆盖旧的，只记录本次失败的任务）
    if os.path.exists(config.bad_output):
        os.remove(config.bad_output)

    # resume 默认 True：output 有产物 → 真断点续跑；无产物 → 全新生成
    done_keys = set()
    is_resuming = config.resume and os.path.exists(config.output)
    if is_resuming:
        try:
            done = tools_jsonl_load(config.output)
            done_keys = {(r["scenario"], r.get("task_id", "")) for r in done}
            logger.info(f"断点续跑：output 里已有 {len(done_keys)} 个任务，跳过")
        except Exception as e:
            logger.warning(f"读取 output 失败，忽略断点续跑：{e}")
    elif os.path.exists(config.output):
        os.remove(config.output)  # 非续跑：清空旧产物重新开始
        logger.info("已关闭续跑：清空旧的 output，重新开始")
    elif config.resume:
        logger.info("output 无产物，全新生成")
    if done_keys:
        items = [it for it in items if (it["scenario"], it["task_id"]) not in done_keys]
    # limit 截断放在这里：先跳过已生成的，再取前 limit 个「未生成」的新任务
    if config.limit is not None:
        items = items[: config.limit]

    logger.info(f"待处理任务：{len(items)} 个（并发 {config.max_concurrency}）")
    if not items:
        logger.info("没有待处理任务，直接结束")
        return

    max_workers = max(1, min(config.max_concurrency, len(items)))  # 并发数不超过任务数
    done_count = 0
    pbar = tqdm(total=len(items), desc="生成示例数据", unit="任务")
    with pbar, ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_item = {
            pool.submit(generate_sample_for_task, it, it["db_item"], config): it  # 每个任务一个线程
            for it in items
        }
        for future in as_completed(future_to_item):  # 谁先完成先处理谁
            item = future_to_item[future]
            try:
                r = future.result()
            except Exception as e:  # 线程内异常（未捕获的代码 bug）→ 清空该任务数据，避免脏数据残留
                logger.error(f"{item['scenario']}/{item['task_id']}: 生成过程异常 {e}")
                _clear_db_data_task(item["scenario"], item["task_id"], item["db_item"].get("db_path", ""))
                _append_result({"scenario": item["scenario"], "task_id": item["task_id"], "task": item["task"], "reason": f"异常: {e}"}, config.bad_output)
                pbar.update(1)
                continue
            if r is not None:
                if "reason" in r:   # 失败记录 → 写 bad_tasks 清单
                    _append_result(r, config.bad_output)
                    logger.warning(f"✗ {r['scenario']}/{r['task_id']}: 放弃该任务（{r['reason'][:80]}）")
                else:               # 成功 → 写 output
                    _append_result(r, config.output)   # 增量落盘（中断/断网不丢已完成）
                    done_count += 1
                    logger.success(f"✓ {r['scenario']}/{r['task_id']}: {r['inserts_count']} 条 INSERT（{done_count}/{len(items)}）")
            else:
                logger.warning(f"✗ {item['scenario']}/{item['task_id']}: 放弃该任务（未 100% 零错误）")
            pbar.update(1)
    pbar.close()

    logger.info(f"处理任务：{done_count}/{len(items)}，结果已增量写入 {config.output}")
def main():
    from simpleArgParser import parse_args
    config: Config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)


