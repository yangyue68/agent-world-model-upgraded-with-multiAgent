"""单任务多角色流程；分组串行生成、全局评审、定向修复。"""

import hashlib
import json
from pathlib import Path

from loguru import logger
from lib.interrupt import check_cancelled
from lib.tools import safe_dir_name
from .llm import ReplyError, build_agents, request_json
from .validators import normalize_groups, parse_group, parse_plan, parse_review, validate_spec


def format_context(item, config):
    sample = item["sample_item"]
    snapshot = sample.get("initial_snapshot")
    if isinstance(snapshot, dict):
        examples = {name: {"columns": table.get("columns", []),
                           "rows": table.get("rows", [])[:config.sample_per_table],
                           "total_rows": len(table.get("rows", []))}
                    for name, table in snapshot.items()}
    else:
        examples = [{"table_name": t.get("table_name"),
                     "insert_statements": t.get("insert_statements", [])[:config.sample_per_table]}
                    for t in sample.get("sample_data", {}).get("tables", [])]
    # 不截断任务、DDL 和接口；示例抽样是显式的配置，并说明不是全库。
    return json.dumps({"scenario": item["scenario"], "task": item["task"],
                       "database_schema": item["db_item"]["db_schema"],
                       "sample_rows_partial_not_task_answers": examples}, ensure_ascii=False)


def generate_spec_for_task(item, config, agents=None):
    label = f"{item['scenario']}/{item['task_id']}"
    identity = hashlib.sha256(label.encode()).hexdigest()[:12]
    directory = Path(config.output).parent / "_raw_llm" / (safe_dir_name(label)[:100] + "_" + identity)
    agents = agents if agents is not None else build_agents(config, label, directory)
    context = format_context(item, config)
    base = {k: item[k] for k in ("scenario", "task_id", "task")}

    def failure(stage, reason):
        logger.error(f"[{label}] {stage} 失败：{reason}")
        return {**base, "stage": stage, "reason": reason}

    try:
        plan = request_json(agents["planner"], context, parse_plan, config.plan_retry, label + "/planner")
    except ReplyError as exc:
        return failure("planner", str(exc))
    logger.info(f"[{label}] 规划分组：{plan}")
    groups = []
    for name in plan:
        prompt = context + "\n" + json.dumps({"group_to_design": name, "ordered_plan": plan,
                                             "already_implemented_groups": groups}, ensure_ascii=False)
        try:
            group = request_json(agents["designer"], prompt,
                                 lambda data: parse_group(data, name, allow_empty=bool(groups)),
                                 config.generation_retry, label + "/designer/" + name)
        except ReplyError as exc:
            # 不把缺失分组当成功，不拿残缺规范交给 reviewer。
            return failure("designer", str(exc))
        if group["endpoints"]:
            groups.append(group)
        else:
            logger.info(f"[{label}] {name} 无独立接口，交由后续全局评审确认覆盖")

    last_problems = {}
    for round_id in range(config.max_retry + 1):
        check_cancelled()
        groups = normalize_groups(groups)
        problems = validate_spec(groups)
        if not problems:
            prompt = context + "\nAPI specification:\n" + json.dumps({"api_groups": groups}, ensure_ascii=False)
            try:
                verdicts = request_json(agents["reviewer"], prompt, lambda data: parse_review(data, groups),
                                        config.review_retry, label + "/reviewer")
            except ReplyError as exc:
                # Reviewer 格式/服务失败只重试 Reviewer，不能驱动 Fixer 修改正确业务。
                return failure("reviewer", str(exc))
            problems = {v["group"]: [v["issue"]] for v in verdicts if not v["pass"]}
            if not problems:
                logger.success(f"[{label}] 通过：{len(groups)} 个分组，"
                               f"{sum(len(g['endpoints']) for g in groups)} 个接口")
                return {**base, "spec_version": "specific-v1", "api_spec": {"api_groups": groups}}
        last_problems = problems
        logger.warning(f"[{label}] 第 {round_id + 1} 轮检查：{json.dumps(problems, ensure_ascii=False)}")
        if round_id == config.max_retry:
            break
        for index, group in enumerate(groups):
            name = group["group_name"]
            if name not in problems:
                continue
            prompt = context + "\n" + json.dumps({"group_to_fix": name, "problems": problems[name],
                                                 "current_api_spec": {"api_groups": groups}}, ensure_ascii=False)
            try:
                fixed = request_json(agents["fixer"], prompt, lambda data: parse_group(data, name),
                                     config.generation_retry, label + "/fixer/" + name)
            except ReplyError as exc:
                logger.warning(f"[{label}] 保留 {name} 当前版本：{exc}")
                last_problems.setdefault(name, []).append(str(exc))
                continue
            fixed = normalize_groups([fixed])[0]
            if fixed == group:
                logger.warning(f"[{label}] Fixer 未改动 {name}，不会当作修复成功")
            else:
                logger.info(f"[{label}] 应用 {name} 修复：{len(group['endpoints'])} → {len(fixed['endpoints'])} 个接口")
                groups[index] = fixed
        # 下一轮永远验证最新规范，包括最后一次修复。
    return failure("validation", "修复次数耗尽：" + json.dumps(last_problems, ensure_ascii=False))
