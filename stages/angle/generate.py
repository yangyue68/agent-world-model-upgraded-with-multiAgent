"""多 agent 版角度生成（从 task 阶段拆分出来的独立阶段，流水线第 2 步）。

流程：
  Planner(规划角度) → 角度语义评审(逐条) → 合格角度不足则带「上一轮角度 + 失败原因」重规划（方案 A）。

运行方式（在项目根目录 ma_upgrade/ 下）：
  python -m stages.angle.generate
"""

import json
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

# 确保项目根目录在 sys.path 上，便于 import awm / ma_upgrade
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 默认输出文件用「绝对路径」，与 CWD 无关
_DEFAULT_OUTPUT = str(_PROJECT_ROOT / "output" / "angle" / "gen_angles_ma.jsonl")
_DEFAULT_INPUT = str(_PROJECT_ROOT / "output" / "scenario"/"gen_scenario_ma.jsonl")
from loguru import logger

from lib.interrupt import run_cli, check_cancelled
from lib.interrupt import InterruptibleThreadPoolExecutor as ThreadPoolExecutor, as_completed
from lib.tools import tools_jsonl_load, tools_jsonl_save, tools_robust_json_loads, normalize_scenario_name
from lib.orchestrator import Agent
from stages.angle.prompts import (
    ANGLE_PLANNER_SYSTEM_PROMPT,
    ANGLE_PLANNER_USER_PROMPT,
    ANGLE_REVIEWER_SYSTEM_PROMPT,
)
from tqdm import tqdm

NUM_ANGLES = 5   # 每个场景固定保留的合格角度数


@dataclass
class Config:
    """角度生成的配置项。"""
    input: str  = _DEFAULT_INPUT                              # 场景文件（gen_scenario_ma.jsonl）
    output: str = _DEFAULT_OUTPUT             # 角度文件（默认绝对路径）
    num_angles: int = NUM_ANGLES              # 每个场景要保留的合格角度数
    shuffle: bool = False                     # 是否打乱场景顺序
    limit: int | None = None                  # 只处理前 limit 个场景（None 表示全部）
    model: str = "your-llm-model-name"        # 模型名（占位，必须被覆盖）
    max_retry: int = 1                        # 规划 + 评审循环最大轮次
    max_concurrency: int = 20                  # 场景级并发数
    resume: bool = True                       # 是否断点续跑（默认续跑）
    max_tokens=50000

    def pre_process(self):
        """开工前检查：补齐默认值、校验输入、解析模型名。"""
        if self.limit is None:
            self.limit = int(1e18)
        assert self.input and os.path.exists(self.input), "input is required"
        if os.environ.get("AWM_SYN_OVERRIDE_MODEL"):
            self.model = os.environ.get("AWM_SYN_OVERRIDE_MODEL")
        assert self.model != "your-llm-model-name", "请设置模型名（--model 或 AWM_SYN_OVERRIDE_MODEL）"


def build_agents(config: Config) -> dict:
    """构建 planner / angle_reviewer 两个 agent。"""
    return {
        "planner": Agent(
            "angle_planner", ANGLE_PLANNER_SYSTEM_PROMPT,
            model=config.model, temperature=0.3, max_tokens=config.max_tokens,
        ),
        "angle_reviewer": Agent(
            "angle_reviewer", ANGLE_REVIEWER_SYSTEM_PROMPT,
            model=config.model, temperature=0.0, max_tokens=config.max_tokens,
        ),
    }


def _plan(scenario_name: str, scenario_desc: str, feedback: str, agents: dict, config: Config) -> list:
    """① 规划角度。feedback 含「上一轮角度 + 失败原因」，非空时在上一轮基础上修正。"""
    prompt = ANGLE_PLANNER_USER_PROMPT.format(
        scenario_name=scenario_name,
        scenario_description=scenario_desc,
        num_angles=config.num_angles,
    )
    if feedback:
        prompt += feedback   # 带上上一轮角度 + 失败原因
    try:
        plan = agents["planner"].run_json(prompt)
        if not isinstance(plan, dict):
            plan = {"task_angles": plan} if isinstance(plan, list) else {}
        return plan.get("task_angles", [])
    except Exception as e:
        logger.warning(f"{scenario_name}: 规划失败（{e}）")
        return []


def _review_angles(angles: list, scenario_name: str, scenario_desc: str, agents: dict) -> dict:
    """② 角度语义评审。返回 {角度文本: {"pass": bool, "issue": str}}。"""
    payload = {"scenario": scenario_name, "description": scenario_desc, "angles": angles}
    try:
        review = agents["angle_reviewer"].run_json(json.dumps(payload, ensure_ascii=False))
        if not isinstance(review, dict):
            review = {}
        results = review.get("results", [])
        if not isinstance(results, list):
            results = []
    except Exception as e:
        logger.error(f"角度评审解析失败: {e}")
        results = []
    verdicts = {}
    for r in results:
        if isinstance(r, dict) and r.get("angle"):
            verdicts[r["angle"].strip()] = {"pass": bool(r.get("pass")), "issue": r.get("issue", "")}
    return verdicts


def generate_angles_for_scenario(scenario: dict, config: Config):
    """对单个场景：规划角度 → 评审 → 合格不足则带「上一轮角度 + 失败原因」重规划（方案 A）。"""
    agents = build_agents(config)
    scenario_name = scenario["name"]
    scenario_desc = scenario.get("description", scenario_name)

    feedback = ""   # 上一轮角度 + 失败原因，首轮为空
    for attempt in range(config.max_retry + 1):
        check_cancelled()
        raw_angles = _plan(scenario_name, scenario_desc, feedback, agents, config)
        if not raw_angles:
            feedback = "\n\n【规划失败，未生成任何角度，请重新规划】"
            logger.warning(f"{scenario_name}: 规划失败（第 {attempt + 1} 轮）")
            continue

        angle_verdicts = _review_angles(raw_angles, scenario_name, scenario_desc, agents)
        angles = [a for a in raw_angles if angle_verdicts.get(a.strip(), {}).get("pass")]
        if len(angles) >= config.num_angles:
            return {"scenario": scenario_name, "angles": angles[: config.num_angles]}

        # 合格不足 → 把「上一轮角度 + 失败原因」反馈给 planner，让它在上一次基础上修正
        failed = [
            angle_verdicts.get(a.strip(), {}).get("issue", "未知原因")
            for a in raw_angles
            if not angle_verdicts.get(a.strip(), {}).get("pass")
        ]
        feedback = (
            "\n\n【上一轮规划的角度】\n" + json.dumps(raw_angles, ensure_ascii=False)
            + "\n【未通过评审的角度及原因】\n" + json.dumps(failed, ensure_ascii=False)
            + "\n请在上一次角度基础上修正/替换不合格角度，重新规划。"
        )
        logger.warning(f"{scenario_name}: 合格角度不足（{len(angles)}/{config.num_angles}），重试（{attempt + 1}/{config.max_retry + 1}）")

    logger.error(f"{scenario_name}: 合格角度不足 {config.num_angles} 个，放弃该场景")
    return None


def _append_result(result: dict, output_path: str):
    """把单个场景结果追加写一行到 output（JSONL 追加模式）。"""
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def run(config: Config):
    """入口：读取场景 → 断点续跑过滤 → 并发生成角度 → 每完成一个场景增量落盘。"""
    logger.info("多 agent 角度生成开始 ...")
    scenarios = tools_jsonl_load(config.input)
    if config.shuffle:
        random.shuffle(scenarios)

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
        scenarios = [s for s in scenarios if normalize_scenario_name(s.get("name", "")) not in done_names]
    # limit 截断放在这里：先跳过已生成的，再取前 limit 个「未生成」的新场景
    if config.limit is not None:
        scenarios = scenarios[: config.limit]

    logger.info(f"待处理场景：{len(scenarios)} 个（并发 {config.max_concurrency}）")
    if not scenarios:
        logger.info("没有待处理场景，直接结束")
        return

    max_workers = max(1, min(config.max_concurrency, len(scenarios)))
    done_count = 0
    pbar = tqdm(total=len(scenarios), desc="生成角度", unit="场景")
    with pbar, ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_scenario = {pool.submit(generate_angles_for_scenario, s, config): s for s in scenarios}
        for future in as_completed(future_to_scenario):
            scenario = future_to_scenario[future]
            try:
                r = future.result()
            except Exception as e:
                logger.error(f"{scenario['name']}: 生成过程异常 {e}")
                pbar.update(1)
                continue
            if r is not None:
                _append_result(r, config.output)
                done_count += 1
                logger.success(f"✓ {r['scenario']}: {len(r['angles'])} 个角度（{done_count}/{len(scenarios)}）")
            else:
                logger.warning(f"✗ {scenario['name']}: 放弃")
            pbar.update(1)
    pbar.close()

    logger.info(f"处理场景：{done_count}/{len(scenarios)}，角度保存到 {config.output}")


def main():
    from simpleArgParser import parse_args
    config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)
