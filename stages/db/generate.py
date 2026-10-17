"""
多 agent 版本的数据库表结构生成（改造 awm/core/db.py，流水线第 3 步）。

流程：
  ① SchemaDesigner(设计表结构) → ② 规则校验(确定性)
  → ③ SQLReviewer(整体评审+逐表verdicts) → ④ 真正建库(复用 create_sqlite_database) → ⑤ Fixer 修复

运行方式（在项目根目录 ma_upgrade/ 下）：
python -m stages.db.generate
"""

import json     # 把评审意见/建库错误序列化成 JSON 字符串
import os       # 判断文件是否存在、创建目录
import sys      # 把项目根目录加进 sys.path
from dataclasses import dataclass   # 用数据类定义配置项
from pathlib import Path            # 处理文件路径

# 确保项目根目录在 sys.path 上，便于 import awm / ma_upgrade
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent   # 上溯四级到项目根目录
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 默认输出/建库目录用「绝对路径」，与 CWD 无关
_DEFAULT_OUTPUT = str(_PROJECT_ROOT / "output" / "db" / "gen_db_ma.jsonl")
_DEFAULT_DB_DIR = str(_PROJECT_ROOT / "output" / "db" / "databases")
_DEFAULT_BAD_OUTPUT = str(_PROJECT_ROOT / "output" / "db" / "db_bad_tasks.jsonl")
_DEFAULT_TASK= str(_PROJECT_ROOT / "output" / "task" / "gen_tasks_ma.jsonl")
from loguru import logger   # 日志工具

from lib.interrupt import run_cli, check_cancelled
from lib.interrupt import InterruptibleThreadPoolExecutor as ThreadPoolExecutor, as_completed
from stages.db.db_utils import create_sqlite_database   # 复用原项目的建库函数（真正执行 DDL）
from lib.tools import tools_jsonl_load, tools_jsonl_save, tools_robust_json_loads, normalize_scenario_name, safe_dir_name

from lib.orchestrator import Agent   # 通用 agent 原语
from stages.db.prompts import (   # 本阶段的提示词
    SCHEMA_DESIGN_SYSTEM_PROMPT,
    SCHEMA_DESIGN_USER_PROMPT,
    SCHEMA_REVIEW_SYSTEM_PROMPT,
    SCHEMA_FIX_SYSTEM_PROMPT,
)
from stages.db.validators import validate_tables, DB_RULES   # 本阶段的规则校验
from tqdm import tqdm   # 进度条


@dataclass
class Config:
    """数据库表结构生成的配置项。"""
    input: str = _DEFAULT_TASK                              # 任务文件（gen_tasks.jsonl）
    output: str = _DEFAULT_OUTPUT             # 输出文件（默认绝对路径）
    bad_output: str = _DEFAULT_BAD_OUTPUT     # 坏任务清单（每次跑完记录失败的任务）
    database_dir: str = _DEFAULT_DB_DIR       # .db 文件存放目录
    model: str = "your-llm-model-name"        # 模型名（占位，必须被覆盖）
    allowed_scenarios: list[str] | None = None  # 只处理这些场景
    limit: int | None = None                  # 只处理前 limit 个场景
    max_retry: int = 3                        # 设计+评审循环最大轮次
    max_concurrency: int = 3                  # 任务级并发数
    resume: bool = True                      # 是否断点续跑（跳过 output 里已完成的场景）
    max_tokens=50000

    def pre_process(self):
        """开工前检查：补齐默认值、校验输入、解析模型名。"""
        if self.limit is None:
            self.limit = int(1e18)
        assert self.input and os.path.exists(self.input), "input is required"
        assert self.output.endswith(".jsonl"), "Output path .jsonl is required"
        if os.environ.get("AWM_SYN_OVERRIDE_MODEL"):
            self.model = os.environ.get("AWM_SYN_OVERRIDE_MODEL")
        assert self.model != "your-llm-model-name", "请设置模型名（--model 或 AWM_SYN_OVERRIDE_MODEL）"



def build_agents(config: Config) -> dict:
    """构建 3 个 agent：表结构设计 / SQL 评审 / 修复。"""
    return {
        "schema_designer": Agent(
            "schema_designer", SCHEMA_DESIGN_SYSTEM_PROMPT,
            model=config.model, temperature=0.3, max_tokens=config.max_tokens,
        ),
        "schema_reviewer": Agent(
            "schema_reviewer", SCHEMA_REVIEW_SYSTEM_PROMPT,
            model=config.model, temperature=0.0, max_tokens=config.max_tokens,
        ),
        "fixer": Agent(
            "fixer", SCHEMA_FIX_SYSTEM_PROMPT,
            model=config.model, temperature=0.0, max_tokens=config.max_tokens,
        ),
    }


def _design(scenario_name: str, tasks: list, feedback: str, agents: dict) -> dict:
    """① 表结构设计。返回 {"tables": [...]}。"""
    tasks_str = "\n".join(f"- {t}" for t in tasks)
    prompt = SCHEMA_DESIGN_USER_PROMPT.format(
        scenario_name=scenario_name,
        tasks_list=tasks_str,
    )
    if feedback:
        prompt += feedback   # 带上上一轮的评审意见
    try:
        r = agents["schema_designer"].run_json(prompt)
        if not isinstance(r, dict):
            r = {"tables": r} if isinstance(r, list) else {}
        return r
    except Exception as e:
        logger.warning(f"{scenario_name}: 表结构设计失败（{e}）")
        return {}


def _review_schema(db_schema: dict, scenario_name: str, tasks: list, agents: dict) -> list:
    """③ SQL 语义评审：整体评审 + 逐表 verdicts。返回 [{"table", "pass", "issue"}]。"""
    payload = {"scenario": scenario_name, "user_tasks": tasks, "db_schema": db_schema}
    try:
        review = agents["schema_reviewer"].run_json(json.dumps(payload, ensure_ascii=False))
        if not isinstance(review, dict):
            review = {}
        results = review.get("results", [])
        if not isinstance(results, list):
            results = []
    except Exception as e:
        logger.error(f"{scenario_name}: SQL 评审解析失败（{e}）")
        results = []
    return results


def _fix_schema(scenario_name: str, tasks: list, db_schema: dict, problems, agents: dict) -> dict:
    """修复：把规则校验/语义评审/建库报错的问题统一交给 fixer 定向修复。返回修复后的 db_schema。"""
    payload = {
        "scenario": scenario_name,
        "user_tasks": tasks,
        "db_schema": db_schema,
        "problems": problems,
    }
    try:
        fixed = agents["fixer"].run_json(json.dumps(payload, ensure_ascii=False))
        if isinstance(fixed, dict) and fixed.get("tables"):
            return fixed
    except Exception as e:
        logger.warning(f"修复失败（{e}）")
    return db_schema   # 修复失败则返回原 schema


def generate_db_for_task(task_item: dict, config: Config):
    """对单个场景：generator 从零生成 → 规则校验/语义评审/建库报错都交给 fixer 修复。"""
    # 每个线程独立构建 agents（独立 GPTClient，避免跨线程共享 httpx 连接池导致挂起）
    agents = build_agents(config)
    scenario_name = task_item["scenario"]
    task_id = task_item["task_id"]
    task = task_item["task"]
    tasks = [task]   # 单任务列表（任务级：只为这一个任务设计表结构）

    db_schema = None
    problems = ""   # 本轮收集的问题（规则校验 + 语义评审 + 建库报错），首轮为空

    for attempt in range(config.max_retry + 1):   # 生成 + 修复循环，最多 max_retry+1 轮
        check_cancelled()
        # 第一次 或 上一轮表为空 → generator 从零生成；否则 → fixer 定向修复
        prev_tables = db_schema.get("tables", []) if isinstance(db_schema, dict) else []
        if attempt == 0 or not prev_tables:
            # generator 从零生成
            db_schema = _design(scenario_name, tasks, "", agents)
        else:
            # fixer 基于上一轮 schema + 问题定向修复
            db_schema = _fix_schema(scenario_name, tasks, db_schema, problems, agents)

        tables = db_schema.get("tables", []) if isinstance(db_schema, dict) else []
        if not tables:
            logger.warning(f"{scenario_name}: 表结构为空（第 {attempt + 1} 轮），交给 generator 重新生成")
            continue

        # ① 规则校验（确定性）：主键 / 无认证字段 / DDL 合法 —— 记录问题，交给 fixer
        _, dropped = validate_tables(tables, DB_RULES)
        rule_problems = [f"表「{d['table']}」: {'; '.join(d['reasons'])}" for d in dropped]

        # ② SQL 语义评审（整体评审 + 逐表 verdicts）—— 记录问题，交给 fixer
        review_problems = []
        verdicts = _review_schema(db_schema, scenario_name, tasks, agents)
        review_problems = [f"表「{v.get('table')}」: {v.get('issue', '未说明')}" for v in verdicts if not v.get("pass")]

        problems = "\n".join(rule_problems + review_problems)
        if problems:
            logger.warning(f"{scenario_name}: 规则/语义校验发现问题（第 {attempt + 1} 轮）：{problems}")
            continue

        # ③ 真正建库（复用 create_sqlite_database，真正执行 DDL），.db 统一放到 config.database_dir
        safe_scenario = safe_dir_name(scenario_name)
        db_filename = f"{safe_scenario}_{task_id}.db"
        db_path, success, failed_count, errors = create_sqlite_database(scenario_name, db_schema, config.database_dir, db_filename)
        if failed_count == 0:                # 全部建表成功 → 返回
            db_path = os.path.relpath(db_path, _PROJECT_ROOT).replace(os.sep, "/")   # 相对路径（用 / 分隔，跨平台），便于迁移
            return {"scenario": scenario_name, "task_id": task_id, "task": task, "db_schema": db_schema, "db_path": db_path}

        # ④ 建库报错 → 交给 fixer（下一轮修复）
        problems = "【建库报错】\n" + "\n".join(str(e) for e in list(errors))
        logger.warning(f"{scenario_name}: 建库报错（第 {attempt + 1} 轮），交给 fixer 修复")

    # 重试耗尽 → 放弃该任务，返回失败记录（含原因，供 bad_tasks 清单）
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
    """入口：读取任务 → 展开成任务级 item → 断点续跑过滤 → 并发生成表结构并建库 → 每完成一个任务增量落盘。"""
    logger.info("多 agent 数据库表结构生成开始 ...")
    scenarios = tools_jsonl_load(config.input)
    if config.allowed_scenarios:
        scenarios = [s for s in scenarios if normalize_scenario_name(s["scenario"]) in config.allowed_scenarios]

    # 展开成任务级 item：每个任务独立生成一套 db
    items = []
    for s in scenarios:
        scenario_name = s["scenario"]
        tasks = s.get("tasks", [])
        for i, task in enumerate(tasks, start=1):
            items.append({"scenario": scenario_name, "task_id": f"task_{i}", "task": task})
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
        os.remove(config.output)
        logger.info("已关闭续跑：清空旧的 output，重新开始")
    elif config.resume:
        # 续跑开启但 output 无产物 → 全新生成
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

    # 任务级并发：每完成一个任务就增量落盘
    max_workers = max(1, min(config.max_concurrency, len(items)))
    done_count = 0
    pbar = tqdm(total=len(items), desc="生成表结构", unit="任务")
    with pbar, ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_item = {
            pool.submit(generate_db_for_task, it, config): it
            for it in items
        }
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                r = future.result()
            except Exception as e:
                logger.error(f"{item['scenario']}/{item['task_id']}: 生成过程异常 {e}")
                _append_result({"scenario": item["scenario"], "task_id": item["task_id"], "task": item["task"], "reason": f"异常: {e}"}, config.bad_output)
                pbar.update(1)
                continue
            if r is not None:
                if "reason" in r:   # 失败记录 → 写 bad_tasks 清单
                    _append_result(r, config.bad_output)
                    logger.warning(f"✗ {r['scenario']}/{r['task_id']}: 放弃该任务（{r['reason'][:80]}）")
                else:               # 成功 → 写 output
                    _append_result(r, config.output)
                    done_count += 1
                    logger.success(f"✓ {r['scenario']}/{r['task_id']}: 表结构已生成并建库（{done_count}/{len(items)}）")
            else:
                logger.warning(f"✗ {item['scenario']}/{item['task_id']}: 放弃该任务")
            pbar.update(1)
    pbar.close()

    logger.info(f"处理任务：{done_count}/{len(items)}，结果保存到 {config.output}")


def main():
    from simpleArgParser import parse_args
    config: Config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)
