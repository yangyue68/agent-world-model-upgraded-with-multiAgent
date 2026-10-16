"""多 agent 版本的任务生成（从 task 阶段拆分出 angle 后的独立阶段，流水线第 3 步）。

流程：
  读取场景 + 角度 → map_reduce(每角度并行生成任务) → 规则校验 → 任务语义评审
  → 不合格则 fixer 带「上一轮任务 + 失败原因」定向修复（方案 B）。

运行方式（在项目根目录 ma_upgrade/ 下）：
  python -m stages.task.generate \
      --input_scenario output/scenario/gen_scenario_ma.jsonl \
      --input_angles output/angle/gen_angles_ma.jsonl
"""

import json
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_DEFAULT_OUTPUT = str(_PROJECT_ROOT / "output" / "task" / "gen_tasks_ma.jsonl")
_DEFAULT_ANGLES = str(_PROJECT_ROOT / "output" / "angle" / "gen_angles_ma.jsonl")

from loguru import logger

from lib.interrupt import run_cli, check_cancelled
from lib.interrupt import InterruptibleThreadPoolExecutor as ThreadPoolExecutor, as_completed
from lib.tools import tools_jsonl_load, normalize_scenario_name
from lib.orchestrator import Agent
from stages.task.prompts import (
    TASK_GENERATION_SYSTEM_PROMPT_MA,
    TASK_GENERATION_USER_PROMPT_MA,
    TASK_REVIEWER_SYSTEM_PROMPT,
    TASK_FIXER_SYSTEM_PROMPT,
)
from stages.task.validators import validate_tasks_paired, TASK_RULES
from tqdm import tqdm



@dataclass
class Config:
    """任务生成的配置项。"""
    input_scenario: str                         # 场景文件（gen_scenario_ma.jsonl）
    input_angles: str = _DEFAULT_ANGLES         # 角度文件（gen_angles_ma.jsonl）
    output: str = _DEFAULT_OUTPUT               # 输出文件（默认绝对路径）
    num_tasks: int =  10                        # 每个场景要保留的合格任务数，这个参数实际不起作用了，因为我们不需要截断到10，合格的任务都可以保留下来
    shuffle: bool = False                       # 是否打乱场景顺序
    limit: int | None = None                    # 只处理前 limit 个场景
    model: str = "your-llm-model-name"          # 模型名（占位，必须被覆盖）
    max_retry: int = 1                          # 生成 + 修复循环最大轮次
    max_concurrency: int = 10                    # 场景级并发数
    resume: bool = True                         # 是否断点续跑（默认续跑）
    tasks_per_angle:int=3                       # 每个角度固定生成 3 条任务（任务级下游互不约束，多任务=多数据，无需限制）
    max_tokens: int = 50000

    def pre_process(self):
        if self.limit is None:
            self.limit = int(1e18)
        assert self.input_scenario and os.path.exists(self.input_scenario), "input_scenario is required"
        assert self.input_angles and os.path.exists(self.input_angles), "input_angles is required"
        if os.environ.get("AWM_SYN_OVERRIDE_MODEL"):
            self.model = os.environ.get("AWM_SYN_OVERRIDE_MODEL")
        assert self.model != "your-llm-model-name", "请设置模型名（--model 或 AWM_SYN_OVERRIDE_MODEL）"


def build_agents(config: Config) -> dict:
    """构建 generator / reviewer / fixer 三个 agent。"""
    return {
        "generator": Agent(
            "task_generator", TASK_GENERATION_SYSTEM_PROMPT_MA,
            model=config.model, temperature=1.0, max_tokens=config.max_tokens,
        ),
        "reviewer": Agent(
            "task_reviewer", TASK_REVIEWER_SYSTEM_PROMPT,
            model=config.model, temperature=0.0, max_tokens=config.max_tokens,
        ),
        "fixer": Agent(
            "task_fixer", TASK_FIXER_SYSTEM_PROMPT,
            model=config.model, temperature=0.0, max_tokens=config.max_tokens,
        ),
    }


def _build_gen_input(scenario_name: str, scenario_desc: str, angle: str, num_tasks: int) -> str:
    """map 阶段：把一个任务角度包装成一次生成提示词（从零生成）。"""
    prompt = TASK_GENERATION_USER_PROMPT_MA.format(
        scenario_name=scenario_name,
        scenario_description=scenario_desc,
        num_tasks=num_tasks,
    )
    prompt += f"\n\n本次请聚焦这个任务角度（类型）：{angle}"
    return prompt


def _review(kept_paired: list, scenario_name: str, scenario_desc: str, agents: dict) -> list:
    """任务语义评审，返回 [{"angle", "task", "pass", "issue"}]。"""
    payload = {"scenario": scenario_name, "description": scenario_desc, "angle_tasks": kept_paired}
    try:
        review = agents["reviewer"].run_json(json.dumps(payload, ensure_ascii=False))
        if not isinstance(review, dict):
            review = {}
        results = review.get("results", [])
        if not isinstance(results, list):
            results = []
    except Exception as e:
        logger.error(f"评审解析失败: {e}")
        results = []
    verdicts = []
    for r in results:
        if isinstance(r, dict) and r.get("task"):
            verdicts.append({
                "angle": r.get("angle", "未知角度"),
                "task": r["task"].strip(),
                "pass": bool(r.get("pass")),
                "issue": r.get("issue", ""),
            })
    return verdicts


def _fix_tasks(scenario_name: str, scenario_desc: str, angle_tasks: list, problems: str, agents: dict) -> list:
    """fixer 带上一轮的「角度→任务」配对 + 失败原因定向修复。返回修复后的配对。"""
    payload = {
        "scenario": scenario_name,
        "description": scenario_desc,
        "angle_tasks": angle_tasks,
        "problems": problems,
    }
    try:
        fixed = agents["fixer"].run_json(json.dumps(payload, ensure_ascii=False))
        if isinstance(fixed, dict) and fixed.get("angle_tasks"):
            return fixed["angle_tasks"]
    except Exception as e:
        logger.warning(f"修复失败（{e}）")
    return angle_tasks   # 修复失败则返回原配对


def generate_tasks_for_angle(angle: str, scenario_name: str, scenario_desc: str, tasks_per_angle: int, config: Config):
    """对一个 angle：generator 从零生成 → 规则校验 → 语义评审 → fixer 修复（闭环）。"""
    agents = build_agents(config)   # 每个 angle 独立构建 agents，避免跨线程共享 httpx 连接池
    angle_tasks = None   # [{"angle": angle, "tasks": [...]}]，初始 None
    problems = ""

    for attempt in range(config.max_retry + 1):
        check_cancelled()
        if attempt == 0 or angle_tasks is None:
            # generator 从零生成这一个 angle 的 tasks
            parsed = agents["generator"].run_json(_build_gen_input(scenario_name, scenario_desc, angle, tasks_per_angle))
            if isinstance(parsed, dict):
                tasks = parsed.get("tasks", [])
            elif isinstance(parsed, list):
                tasks = [t for t in parsed if isinstance(t, str)]
            else:
                tasks = []
            angle_tasks = [{"angle": angle, "tasks": tasks}]
        else:
            # fixer 带上一轮任务 + 失败原因定向修复
            angle_tasks = _fix_tasks(scenario_name, scenario_desc, angle_tasks, problems, agents)

        #angle_tasks是个列表形式，实际上只有一个字典元素，所以angle_tasks[0]就是当前angle及其对应的tasks
        tasks = angle_tasks[0].get("tasks", []) if angle_tasks else []
        if not tasks:
            logger.warning(f"{scenario_name}/{angle}: 任务为空（第 {attempt + 1} 轮），交给 generator 重新生成")
            angle_tasks = None
            continue

        # 规则校验（配对结构，dropped 带 angle）
        kept_paired, dropped = validate_tasks_paired(angle_tasks, TASK_RULES)
        rule_problems = [f"角度「{d['angle']}」的任务「{d['task']}」原因: {'; '.join(d['reasons'])}" for d in dropped]


        # 语义评审（评审「规则通过的」kept_paired）
        passed = []
        review_problems = []
        if kept_paired:
            verdicts = _review(kept_paired, scenario_name, scenario_desc, agents)
            passed = [v["task"] for v in verdicts if v["pass"]]
            review_problems = [f"角度「{v['angle']}」的任务「{v['task']}」原因: {v['issue']}" for v in verdicts if not v["pass"]]

        problems = "\n".join(rule_problems + review_problems)
        if problems:
            logger.warning(f"{scenario_name}/{angle}: 规则/语义校验发现问题（第 {attempt + 1} 轮），交给 fixer 修复")
            # 保持 angle_tasks 不变（完整任务，含规则被剔除的），让 fixer 能看到所有有问题的任务并针对性修复
            continue

        return {"angle": angle, "tasks": passed}

    return None


def generate_tasks_for_scenario(scenario: dict, angles: list, config: Config):
    """对单个场景：多个 angle 并行，每个 angle 内部完成「生成→校验→评审→修复」闭环。"""
    scenario_name = scenario["name"]
    scenario_desc = scenario.get("description", scenario_name)


    # angle 并发度 = 该场景下 angle 的数量
    results = []
    with ThreadPoolExecutor(max_workers=len(angles)) as pool:
        futures = [pool.submit(generate_tasks_for_angle, a, scenario_name, scenario_desc, config.tasks_per_angle, config) for a in angles]
        for future in as_completed(futures):
            try:
                r = future.result()
            except Exception as e:
                logger.error(f"{scenario_name}: angle 处理异常 {e}")
                continue
            if r and r.get("tasks"):
                results.append(r)

    # 合并各 angle 的任务：生成扁平的 tasks 列表供下游读取，task_meta 保留 angle 归属（仅写入产物，不再建子目录）
    task_meta = [{"task": t, "angle": r["angle"]} for r in results for t in r["tasks"]]
    all_tasks = [m["task"] for m in task_meta]
    if not all_tasks:
        logger.error(f"{scenario_name}: 没有任何合格任务，放弃该场景")
        return None
    return {"scenario": scenario_name, "tasks": all_tasks, "angles": angles, "task_meta": task_meta}


def _append_result(result: dict, output_path: str):
    """把单个场景结果追加写一行到 output（JSONL 追加模式）。"""
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def run(config: Config):
    """入口：读取场景 + 角度 → 断点续跑过滤 → 并发生成任务 → 增量落盘。"""
    logger.info("多 agent 任务生成开始 ...")
    scenarios = tools_jsonl_load(config.input_scenario)
    angles_data = tools_jsonl_load(config.input_angles)
    angles_map = {normalize_scenario_name(a["scenario"]): a.get("angles", []) for a in angles_data}

    if config.shuffle:
        random.shuffle(scenarios)

    # 只处理有角度的场景
    items = [(s, angles_map.get(normalize_scenario_name(s.get("name", "")), [])) for s in scenarios]
    items = [(s, a) for s, a in items if a]

    out_dir = os.path.dirname(config.output) or "."
    os.makedirs(out_dir, exist_ok=True)

    # resume 默认 True：output 有产物 → 真断点续跑；无产物 → 全新生成
    done_names = set()
    is_resuming = config.resume and os.path.exists(config.output)
    if is_resuming:
        try:
            done = tools_jsonl_load(config.output)
            done_names = {normalize_scenario_name(r["scenario"]) for r in done}
            logger.info(f"断点续跑：output 里已有 {len(done_names)} 个场景，跳过")
        except Exception as e:
            logger.warning(f"读取 output 失败，忽略断点续跑：{e}")
    elif os.path.exists(config.output):
        os.remove(config.output)
        logger.info("已关闭续跑：清空旧的 output，重新开始")
    elif config.resume:
        logger.info("output 无产物，全新生成")
    if done_names:
        items = [(s, a) for s, a in items if normalize_scenario_name(s.get("name", "")) not in done_names]
    # limit 截断放在这里：先跳过已生成的，再取前 limit 个「未生成」的新场景
    if config.limit is not None:
        items = items[: config.limit]

    logger.info(f"待处理场景：{len(items)} 个（并发 {config.max_concurrency}）")
    if not items:
        logger.info("没有待处理场景，直接结束")
        return

    max_workers = max(1, min(config.max_concurrency, len(items)))
    done_count = 0
    pbar = tqdm(total=len(items), desc="生成任务", unit="场景")
    with pbar, ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_item = {pool.submit(generate_tasks_for_scenario, s, a, config): s for s, a in items}
        for future in as_completed(future_to_item):
            scenario = future_to_item[future]
            try:
                r = future.result()
            except Exception as e:
                logger.error(f"{scenario['name']}: 生成过程异常 {e}")
                pbar.update(1)
                continue
            if r is not None:
                _append_result(r, config.output)
                done_count += 1
                logger.success(f"✓ {r['scenario']}: {len(r['tasks'])} 个任务（{done_count}/{len(items)}）")
            else:
                logger.warning(f"✗ {scenario['name']}: 放弃")
            pbar.update(1)
    pbar.close()

    logger.info(f"处理场景：{done_count}/{len(items)}，任务保存到 {config.output}")


def main():
    from simpleArgParser import parse_args
    config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)
