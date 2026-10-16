"""
多 agent 任务级 API 规范与环境文件清单生成（流水线第 5 步）。

流程：
  ① SpecPlanner(规划 api_groups) → ② map_reduce(每个 group 并行生成 endpoints)
  → ③ 规则校验与程序生成 files → ④ SpecReviewer(逐 group 语义评审)
  → ⑤ fixer 只修复不过的 group → 重新校验并计算 files。

输入（三个文件）：
  - input_task：任务文件（gen_tasks_ma.jsonl，场景级 {scenario, tasks}）
  - input_db：表结构文件（gen_db_ma.jsonl，任务级 {scenario, task_id, task, db_schema, db_path}）
  - input_sample：第四阶段的样本产物（gen_sample_ma.jsonl，任务级）——只有这里存在的任务才是保留下来的任务

任务级改造：每个任务独立生成一份 API 规范（只为这一个任务的初始状态）。
总文件 output/spec/gen_spec_ma.jsonl 每行一个任务，包含完整 api_spec、files 和 module_contract。
文件清单采用固定骨架，每个 API 分组对应 routers/group_<编号>.py；不要求 LLM 规划目录。

运行方式（在项目根目录 ma_upgrade 下）：
python -m stages.spec.generate




"""

import json     # 把评审意见/规范序列化成 JSON 字符串
import os       # 判断文件是否存在、创建目录
import sys      # 把项目根目录加进 sys.path
import shutil
import tempfile
from dataclasses import dataclass   # 用数据类定义配置项
from pathlib import Path            # 处理文件路径

# ma_upgrade 本身就是项目根目录。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 默认输出文件用「绝对路径」，与 CWD 无关
_DEFAULT_TASK= str(_PROJECT_ROOT / "output" / "task"/"gen_tasks_ma.jsonl")
_DEFAULT_DB=str(_PROJECT_ROOT / "output" / "db"/"gen_db_ma.jsonl")
_DEFAULT_SAMPLE=str(_PROJECT_ROOT / "output" / "sample"/"gen_sample_ma.jsonl")
_DEFAULT_OUTPUT = str(_PROJECT_ROOT / "output" / "spec" / "gen_spec_ma.jsonl")
_DEFAULT_BAD_OUTPUT = str(_PROJECT_ROOT / "output" / "spec" / "spec_bad_tasks.jsonl")

from loguru import logger   # 日志工具

from lib.interrupt import run_cli, check_cancelled
from lib.interrupt import InterruptibleThreadPoolExecutor as ThreadPoolExecutor, as_completed
from lib.tools import (                          # 项目自带的实用小工具
    tools_jsonl_load, tools_jsonl_save, tools_robust_json_loads,
    normalize_scenario_name, format_db_schema, safe_dir_name,
)

from lib.orchestrator import Agent, Orchestrator   # 通用 agent 原语 + 编排器（map_reduce）
from stages.spec.prompts import (   # 本阶段的提示词
    SPEC_PLANNER_SYSTEM_PROMPT,
    SPEC_PLANNER_USER_PROMPT,
    SPEC_DESIGNER_SYSTEM_PROMPT,
    SPEC_DESIGNER_USER_PROMPT,
    SPEC_REVIEWER_SYSTEM_PROMPT,
    SPEC_REVIEWER_USER_PROMPT,
    SPEC_FIX_SYSTEM_PROMPT,
    SPEC_FIX_USER_PROMPT,
)
from stages.spec.validators import validate_spec   # 本阶段的规则校验
from stages.spec.coverage import validate_review
from stages.spec.file_plan import ENV_MODULE_CONTRACT, build_files, build_spec_result, validate_files
from tqdm import tqdm   # 进度条


@dataclass
class Config:
    """API 规范生成的配置项。"""
    input_task: str = _DEFAULT_TASK                           # 任务文件（gen_tasks_ma.jsonl）
    input_db: str  = _DEFAULT_DB                           # 表结构文件（gen_db_ma.jsonl）
    input_sample: str = _DEFAULT_SAMPLE                        # 第四阶段样本产物（gen_sample_ma.jsonl，决定保留哪些场景）
    output: str = _DEFAULT_OUTPUT              # 输出文件（默认绝对路径）
    bad_output: str = _DEFAULT_BAD_OUTPUT      # 坏任务清单（每次跑完记录失败的任务）
    model: str = "your-llm-model-name"         # 模型名（占位，必须被覆盖）
    allowed_scenarios: list[str] | None = None  # 只处理这些场景
    limit: int | None = None                   # 跳过已完成任务后，处理前 limit 个任务
    max_retry: int = 3                         # 生成 + 校验 + 定向修复（fixer）循环最大轮次
    plan_retry: int = 2                        # 规划（SpecPlanner）失败重试次数
    review_retry: int = 1                      # 评审请求/返回格式出错时，只重试 Reviewer
    max_concurrency: int = 4                   # 任务级并发数
    resume: bool = True                       # 是否断点续跑（跳过 output 里已完成的任务）
    sample_per_table: int = 2                 # 每张表抽样的示例数据条数（喂给 planner/designer/reviewer）
    max_tokens: int = 50000                   # 单次 LLM 输出 token 上限

    def pre_process(self):
        """开工前检查：补齐默认值、校验输入、解析模型名。"""
        if self.limit is None:
            self.limit = int(1e18)
        assert self.input_task and os.path.exists(self.input_task), "input_task is required"
        assert self.input_db and os.path.exists(self.input_db), "input_db is required"
        assert self.input_sample and os.path.exists(self.input_sample), "input_sample is required"
        assert self.output.endswith(".jsonl"), "Output path .jsonl is required"
        if os.environ.get("AWM_SYN_OVERRIDE_MODEL"):
            self.model = os.environ.get("AWM_SYN_OVERRIDE_MODEL")
        assert self.model != "your-llm-model-name", "请设置模型名（--model 或 AWM_SYN_OVERRIDE_MODEL）"
        for name in ("max_retry", "plan_retry", "review_retry", "sample_per_table", "limit"):
            if getattr(self, name) is not None and getattr(self, name) < 0:
                raise ValueError(f"{name} 不能为负数")
        for name in ("max_tokens", "max_concurrency"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        if Path(self.output).resolve() == Path(self.bad_output).resolve():
            raise ValueError("output 和 bad_output 不能是同一个文件")



def build_agents(config: Config) -> dict:
    """构建 4 个 agent：规划 / 生成 / 评审 / 修复。"""
    return {
        "spec_planner": Agent(
            "spec_planner", SPEC_PLANNER_SYSTEM_PROMPT,
            model=config.model, temperature=0.3, max_tokens=config.max_tokens,
        ),
        "spec_designer": Agent(
            "spec_designer", SPEC_DESIGNER_SYSTEM_PROMPT,
            model=config.model, temperature=0.5, max_tokens=config.max_tokens,
        ),
        "spec_reviewer": Agent(
            "spec_reviewer", SPEC_REVIEWER_SYSTEM_PROMPT,
            model=config.model, temperature=0.0, max_tokens=config.max_tokens,
        ),
        "spec_fixer": Agent(
            "spec_fixer", SPEC_FIX_SYSTEM_PROMPT,
            model=config.model, temperature=0.5, max_tokens=config.max_tokens,
        ),
    }


def _format_sample(sample_item: dict, per_table: int = 2) -> str:
    """从示例数据里每张表抽样最多 per_table 条 insert，格式化成文本（空表标注为运行时创建）。"""
    lines = []
    tables = (sample_item or {}).get("sample_data", {}).get("tables", [])
    for t in tables:
        table_name = t.get("table_name", "")
        inserts = t.get("insert_statements", []) or []
        picked = inserts[:per_table]
        if not picked:
            lines.append(f"-- table: {table_name} (empty, created at runtime) --")
            continue
        lines.append(f"-- table: {table_name} --")
        lines.extend(picked)
    if not lines:
        return "(no sample data)"
    return "\n".join(lines)


def _plan_groups(scenario_name: str, tasks: list, db_schema: dict, sample_str: str, agents: dict,
                 *, feedback: str = "", diagnostic_dir: Path | None = None, attempt: int = 1) -> list:
    """规划 API 分组；无效响应抛出具体错误，保留原文供诊断。"""
    tasks_str = "\n".join(f"- {t}" for t in tasks)
    schema_str = format_db_schema(db_schema)
    prompt = SPEC_PLANNER_USER_PROMPT.format(
        scenario_name=scenario_name, tasks_list=tasks_str,
        database_schema=schema_str, sample_data=sample_str,
    )
    if feedback:
        prompt += (
            "\n\nPrevious response failed validation: " + feedback
            + '\nReturn exactly {"groups": ["ResourceName"]} with a non-empty list '
            + "of group names for this task. Do not return endpoint specifications or Python code."
        )
    raw = agents["spec_planner"].run(prompt)
    logger.info(f"{scenario_name}: planner attempt={attempt}, raw_length={len(raw)}, raw={raw[:2000]!r}")
    if diagnostic_dir is not None:
        diagnostic_dir.mkdir(parents=True, exist_ok=True)
        raw_path = diagnostic_dir / f"planner_attempt_{attempt}.txt"
        raw_path.write_text(raw, encoding="utf-8")
        logger.info(f"{scenario_name}: planner 原始响应已保存至 {raw_path}")
    if not raw.strip():
        raise ValueError("planner 返回空文本，请检查模型服务错误或响应 content 字段")
    r = tools_robust_json_loads(raw)
    # 名称数组与 {groups: [...]} 含义相同，统一后校验，避免无意义重试。
    if isinstance(r, list):
        logger.info(f"{scenario_name}: planner 返回分组名称数组，转换为 groups 对象")
        r = {"groups": r}
    if not isinstance(r, dict):
        raise ValueError(f"planner JSON 必须为对象，实际为 {type(r).__name__}")
    if "groups" not in r:
        raise ValueError(f"planner JSON 缺少 groups 字段，实际字段为 {list(r.keys())}")
    groups = r["groups"]
    if not isinstance(groups, list):
        raise ValueError(f"planner groups 必须为数组，实际为 {type(groups).__name__}")
    if not groups:
        raise ValueError("planner 明确返回 groups=[]，任务必须至少规划一个业务分组")
    if any(not isinstance(g, str) or not g.strip() for g in groups):
        raise ValueError(f"planner groups 每项必须为非空字符串，实际值为 {groups!r}")
    groups = list(dict.fromkeys(g.strip() for g in groups))
    logger.info(f"{scenario_name}: planner 解析成功，groups={groups}")
    return groups


def _build_group_gen_input(group: str, scenario_name: str, tasks_str: str, schema_str: str, sample_str: str, feedback: str) -> str:
    """为一个 group 拼装生成提示词。"""
    prompt = SPEC_DESIGNER_USER_PROMPT.format(
        scenario_name=scenario_name,
        group_name=group,
        tasks_list=tasks_str,
        database_schema=schema_str,
        sample_data=sample_str,
    )
    if feedback:
        prompt += feedback   # 带上上一轮的评审意见
    return prompt


def _generate_groups(scenario_name: str, tasks: list, db_schema: dict, sample_str: str, groups: list, feedback: str, agents: dict, orch,
                     *, planned_groups: list | None = None, existing_groups: list | None = None) -> list:
    """② map_reduce：每个 group 并行生成 endpoints，返回 [{group_name, endpoints}]。"""
    tasks_str = "\n".join(f"- {t}" for t in tasks)
    schema_str = format_db_schema(db_schema)
    results = orch.map_reduce(
        items=groups,
        worker=agents["spec_designer"],
        to_user_content=lambda g: _build_group_gen_input(g, scenario_name, tasks_str, schema_str, sample_str, feedback)
        + "\n\nOther groups planned for this task (coordinate scope; do not duplicate their endpoints):\n"
        + json.dumps(planned_groups if planned_groups is not None else groups, ensure_ascii=False)
        + "\n\nAlready implemented groups (keep their capabilities; do not duplicate them):\n"
        + json.dumps(existing_groups or [], ensure_ascii=False),
    )
    groups_out = []
    for group, raw in zip(groups, results):
        raw_len = len(raw) if isinstance(raw, str) else 0
        logger.info(f"{scenario_name}: group「{group}」生成 {raw_len} 字符")
        parsed = tools_robust_json_loads(raw)
        if isinstance(parsed, dict):
            endpoints = parsed.get("endpoints", [])
        elif isinstance(parsed, list):
            endpoints = parsed  # 模型偶发输出数组 [{"path","method",...}, ...]
        else:
            endpoints = []
        if (isinstance(parsed, dict) and isinstance(parsed.get("endpoints"), list)) or isinstance(parsed, list):
            # 分组名称由 planner 冻结，designer 不得重命名，否则无法定向修复和绑定 router。
            groups_out.append({"group_name": group, "endpoints": endpoints})
    return groups_out


def _normalize_reviewer_response(response: object, api_groups: list) -> object:
    """Repair superficial reviewer omissions without weakening semantic validation.

    Smaller/private models often understand the task but omit one envelope field in a
    coverage row.  Turning such a row into an explicit uncovered requirement lets the
    normal fixer loop handle it.  We deliberately do not invent operation ids, parameter
    names, lookup sources, or response fields here.
    """
    if not isinstance(response, dict):
        return response
    groups = [g.get("group_name") for g in api_groups if isinstance(g, dict)]
    owners = {
        ep.get("operation_id"): group.get("group_name")
        for group in api_groups if isinstance(group, dict)
        for ep in group.get("endpoints", []) if isinstance(ep, dict) and ep.get("operation_id")
    }

    results = response.get("results")
    if isinstance(results, list):
        normalized_results = []
        for raw in results:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            if not isinstance(row.get("group"), str) or not row["group"].strip():
                alias = row.get("group_name")
                if isinstance(alias, str) and alias.strip():
                    row["group"] = alias.strip()
            if type(row.get("pass")) is not bool:
                row["pass"] = False
                row["issue"] = str(row.get("issue") or "Reviewer omitted a boolean pass value.")
            if not isinstance(row.get("issue"), str):
                row["issue"] = str(row.get("issue") or "")
            normalized_results.append(row)
        response["results"] = normalized_results

    coverage = response.get("coverage")
    if not isinstance(coverage, list):
        return response
    normalized_coverage = []
    for index, raw in enumerate(coverage, 1):
        if not isinstance(raw, dict):
            raw = {}
        row = dict(raw)
        operation_ids = row.get("operation_ids")
        if not isinstance(operation_ids, list):
            operation_ids = []
        operation_ids = [op for op in operation_ids if isinstance(op, str) and op.strip()]
        group = row.get("group")
        if not isinstance(group, str) or group not in groups:
            # Infer ownership only from an operation the model explicitly named.
            inferred = next((owners.get(op) for op in operation_ids if owners.get(op)), None)
            group = inferred or (groups[0] if groups else "")
        requirement = row.get("requirement")
        if not isinstance(requirement, str) or not requirement.strip():
            requirement = f"Reviewer coverage requirement {index}"
        evidence = row.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            evidence = "Reviewer did not provide verifiable evidence."
        issue = row.get("issue")
        if not isinstance(issue, str):
            issue = str(issue or "")
        covered = row.get("covered") is True
        inputs = row.get("inputs")
        # An omitted inputs list is equivalent to an empty list for endpoints with no
        # required request fields. validate_review will still reject a covered row when
        # the referenced operation actually has required fields.
        malformed_input = False
        clean_inputs = []
        if isinstance(inputs, list):
            for item in inputs:
                if not isinstance(item, dict):
                    malformed_input = True
                    continue
                source = item.get("source")
                operation_id = item.get("operation_id")
                parameter = item.get("parameter")
                detail = item.get("detail")
                if (not isinstance(operation_id, str) or not operation_id.strip()
                        or not isinstance(parameter, str) or not parameter.strip()
                        or source not in ("task", "lookup", "context")
                        or not isinstance(detail, str) or not detail.strip()):
                    malformed_input = True
                    continue
                if source == "lookup" and (
                        not isinstance(item.get("lookup_operation_id"), str)
                        or not item.get("lookup_operation_id", "").strip()
                        or not isinstance(item.get("response_field"), str)
                        or not item.get("response_field", "").strip()):
                    malformed_input = True
                    continue
                clean_inputs.append(dict(item))
        if malformed_input:
            covered = False
            operation_ids = []
            clean_inputs = []
            issue = issue or "coverage 输入证据格式不完整，无法证明该要求已覆盖。"
        if not covered:
            operation_ids = []
            clean_inputs = []
            issue = issue or "Reviewer 未证明该任务要求已覆盖。"
        normalized_coverage.append({
            "requirement": requirement,
            "group": group,
            "covered": covered,
            "operation_ids": operation_ids,
            "inputs": clean_inputs,
            "evidence": evidence,
            "issue": issue,
        })
    response["coverage"] = normalized_coverage
    return response


def _review(api_groups: list, scenario_name: str, tasks: list, db_schema: dict, sample_str: str, agents: dict,
            *, max_retry: int = 1, diagnostic_dir: Path | None = None, round_number: int = 1) -> list:
    """④ 评审 + 逐项覆盖证据核对；评审格式失败只重试评审，不修改接口。"""
    tasks_str = "\n".join(f"- {t}" for t in tasks)
    schema_str = format_db_schema(db_schema)
    api_spec_json = json.dumps({"api_groups": api_groups}, ensure_ascii=False)
    user_content = SPEC_REVIEWER_USER_PROMPT.format(
        scenario_name=scenario_name,
        tasks_list=tasks_str,
        database_schema=schema_str,
        sample_data=sample_str,
        api_spec_json=api_spec_json,
    )
    user_content += (
        "\n\nDeterministic file manifest (fixed layout, not a redesign request):\n"
        + json.dumps(build_files({"api_groups": api_groups}), ensure_ascii=False)
        + "\nShared module contract:\n" + json.dumps(ENV_MODULE_CONTRACT, ensure_ascii=False)
    )
    error = ""
    saw_coverage_payload = False
    for review_attempt in range(max_retry + 1):
        check_cancelled()
        response = None
        verdicts = None
        try:
            prompt = user_content
            if error:
                prompt += (
                    "\n\nYour previous review could not be validated. Return a complete, minimal JSON "
                    "review now; do not change the API specification. Every coverage row MUST contain "
                    "requirement, group, covered (boolean), operation_ids (array), inputs (array), "
                    "evidence and issue. For a lookup input, always include both lookup_operation_id "
                    "and response_field. If you cannot prove a row, set covered=false, operation_ids=[], "
                    "inputs=[] and explain the missing evidence in issue. Do not omit the row and do "
                    "not return prose. Validation error:\n" + error
                )
            response = agents["spec_reviewer"].run_json(prompt)
            # Private models frequently return a mostly useful coverage list while omitting one
            # field. Normalize only the response envelope here; validate_review remains strict
            # and will still reject invented operations, parameters, lookup fields, or types.
            response = _normalize_reviewer_response(response, api_groups)
            if isinstance(response, dict) and isinstance(response.get("coverage"), list) and response.get("coverage"):
                saw_coverage_payload = True
            verdicts = validate_review(api_groups, response)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning(f"{scenario_name}: Reviewer 第 {review_attempt + 1}/{max_retry + 1} 次失败：{error}")
        if diagnostic_dir is not None:
            try:
                diagnostic_dir.mkdir(parents=True, exist_ok=True)
                path = diagnostic_dir / f"review_round_{round_number}_attempt_{review_attempt + 1}.json"
                path.write_text(json.dumps({"response": response, "verdicts": verdicts,
                                            "error": error if verdicts is None else None},
                                           ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, TypeError, ValueError) as exc:
                logger.warning(f"{scenario_name}: 无法保存评审诊断：{exc}")
        if verdicts is not None:
            rows = response["coverage"]
            logger.info(f"{scenario_name}: 覆盖评审 {len(rows)} 项要求，"
                        f"模型声明覆盖 {sum(row['covered'] for row in rows)} 项，"
                        f"程序核对后 {sum(not v['pass'] for v in verdicts)} 个分组不通过")
            for verdict in verdicts:
                if not verdict["pass"]:
                    logger.warning(f"{scenario_name}/{verdict['group']}: {verdict.get('issue', '')}")
            return verdicts
    # A reviewer formatting failure is itself a review failure, but it must not discard an
    # otherwise repairable specification before fixer gets a chance to act.  Keep the failure
    # attached to every group and let the normal retry/fixer loop handle it.
    logger.error(f"{scenario_name}: Reviewer format retries exhausted: {error}")
    return [{"group": g["group_name"], "pass": False,
             "issue": f"评审输出格式无法校验：{error or '未返回有效 coverage'}",
             "coverage": [],
             **({"review_error": True} if not saw_coverage_payload else {})}
            for g in api_groups]


def _build_group_problems(verdicts: list, dropped: list) -> dict:
    """把规则+评审意见按 group 分组，返回 {group_name: problems_str}（每个坏 group 只带自己的问题）。"""
    problems: dict[str, list[str]] = {}
    for d in dropped:
        reasons = d.get("reasons", [])
        problems.setdefault(d.get("group", ""), []).append("规则校验：" + "; ".join(reasons))
    for v in verdicts:
        if not v.get("pass"):
            problems.setdefault(v.get("group", ""), []).append("评审：" + v.get("issue", "未说明"))
    return {name: "\n".join(parts) for name, parts in problems.items()}


def _allows_group_retirement(problems: str) -> bool:
    """Return whether an empty fixer response is an explicit redundancy decision."""
    text = (problems or "").lower()
    # An empty response is commonly a model failure.  Only retire a group when the
    # reviewer explicitly says its capability is duplicated or unnecessary.
    markers = (
        "redundant", "unnecessary", "already covered", "fully covered",
        "冗余", "无需", "不需要", "已覆盖", "重复接口",
    )
    if any(marker in text for marker in markers):
        return True
    # Preserve the explicit legacy reviewer decision used by older records/tests,
    # but do not interpret a deterministic duplicate operation/path error as proof
    # that the entire business group is removable.
    return text.strip() in {"duplicate", "评审：duplicate", "review: duplicate"}


def _has_duplicate_issue(problems: str) -> bool:
    text = (problems or "").lower()
    return "operation_id" in text or "method/path" in text or "重复接口" in text


def _remove_cross_group_duplicates(group: dict, api_groups: list[dict]) -> dict:
    """Remove only endpoints duplicated by another group, preserving unique work."""
    group_name = group.get("group_name")
    other_endpoints = [
        endpoint
        for other in api_groups
        if other.get("group_name") != group_name
        for endpoint in other.get("endpoints", [])
        if isinstance(endpoint, dict)
    ]
    operation_ids = {endpoint.get("operation_id") for endpoint in other_endpoints}
    routes = {(endpoint.get("method"), endpoint.get("path")) for endpoint in other_endpoints}
    kept = [
        endpoint for endpoint in group.get("endpoints", [])
        if endpoint.get("operation_id") not in operation_ids
        and (endpoint.get("method"), endpoint.get("path")) not in routes
    ]
    if len(kept) == len(group.get("endpoints", [])):
        return group
    logger.info(
        f"{group_name}: 确定性移除跨分组重复接口 "
        f"{len(group.get('endpoints', [])) - len(kept)} 个，保留其余接口"
    )
    return {"group_name": group_name, "endpoints": kept}


def _fix_group(group: dict, scenario_name: str, tasks_str: str, schema_str: str,
               sample_str: str, problems: str, agents: dict, api_groups: list | None = None) -> dict:
    """fixer 定向修复单个坏 group。返回修复后的 {group_name, endpoints}（好 endpoint 保留）。"""
    group_name = group.get("group_name", "")
    current = json.dumps({"endpoints": group.get("endpoints", [])}, ensure_ascii=False)
    prompt = SPEC_FIX_USER_PROMPT.format(
        group_name=group_name,
        scenario_name=scenario_name,
        tasks_list=tasks_str,
        database_schema=schema_str,
        sample_data=sample_str,
        current_endpoints=current,
        problems=problems,
    )
    # 全局冲突需要给出其他分组的已占用标识，避免 fixer 再次猜出重名接口。
    if api_groups is not None:
        other_groups = [other for other in api_groups if other.get("group_name") != group_name]
        prompt += ("\n\nFull specifications of other groups (check actual capability before deleting duplicates):\n"
                   + json.dumps(other_groups, ensure_ascii=False))
    logger.info(f"{scenario_name}/{group_name}: fixer 收到的问题：{problems}")
    try:
        raw = agents["spec_fixer"].run(prompt)
        logger.info(
            f"{scenario_name}/{group_name}: fixer raw response length={len(raw)}, "
            f"preview={raw[:2000]!r}"
        )
        try:
            r = tools_robust_json_loads(raw)
        except Exception as parse_error:
            logger.error(
                f"{scenario_name}/{group_name}: fixer response JSON parse failed: "
                f"{parse_error}; keeping original group"
            )
            return group
        if isinstance(r, dict):
            endpoints = r.get("endpoints")
            endpoint_count = len(endpoints) if isinstance(endpoints, list) else "N/A"
            logger.info(
                f"{scenario_name}/{group_name}: fixer parsed type=dict, "
                f"keys={list(r.keys())}, endpoint_count={endpoint_count}"
            )
        else:
            logger.warning(
                f"{scenario_name}/{group_name}: fixer parsed unexpected type="
                f"{type(r).__name__}; keeping original group"
            )
        if isinstance(r, dict) and isinstance(r.get("endpoints"), list):
            logger.info(
                f"{scenario_name}/{group_name}: fixer result ACCEPTED, "
                f"new_endpoint_count={len(r['endpoints'])}"
            )
            return {"group_name": group_name, "endpoints": r["endpoints"]}
        logger.warning(
            f"{scenario_name}/{group_name}: fixer result REJECTED, "
            "endpoints missing or not a list; keeping original group"
        )
    except Exception as e:
        logger.warning(f"{scenario_name}: fixer 修复 group「{group_name}」失败（{e}），原样返回")
    return group   # 修复失败则原样返回


def _normalize_spec_shapes(api_groups: list) -> list:
    """Apply semantics-preserving defaults and unwrap unambiguous route-parameter maps."""
    import re

    def normalize_schema(schema: dict, *, parameter: bool = False):
        if not isinstance(schema, dict):
            return
        kinds = schema.get("type")
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if "object" in kinds:
            required_key = "required_fields" if parameter else "required"
            schema.setdefault(required_key, [])
            properties = schema.get("properties")
            if isinstance(properties, dict):
                for child in properties.values():
                    normalize_schema(child)
        if "array" in kinds:
            normalize_schema(schema.get("items"))

    for group in api_groups:
        if not isinstance(group, dict):
            continue
        for endpoint in group.get("endpoints", []):
            if not isinstance(endpoint, dict):
                continue
            params = endpoint.get("request_params")
            if not isinstance(params, dict):
                continue

            placeholders = set(re.findall(r"\{([^{}]+)\}", endpoint.get("path", "")))
            path_param = params.get("path")
            if placeholders and isinstance(path_param, dict):
                properties = path_param.get("properties")
                if not isinstance(properties, dict) and set(path_param) == placeholders:
                    properties = path_param
                if isinstance(properties, dict) and set(properties) == placeholders:
                    params.pop("path")
                    for name, schema in properties.items():
                        if isinstance(schema, dict):
                            schema["param_type"] = "path"
                            schema["required"] = True
                            params[name] = schema

            for param in params.values():
                normalize_schema(param, parameter=True)
            normalize_schema(endpoint.get("response"))
    return api_groups


def generate_spec_for_task(item: dict, config: Config):
    """对单个任务：规划一次 → map_reduce 生成 → 校验/评审 → fixer 只定向修复坏 group（好 group 保留），循环到全过或重试耗尽。"""
    # 每个线程独立构建 agents（独立 GPTClient，避免跨线程共享 httpx 连接池导致挂起）
    agents = build_agents(config)
    orch = Orchestrator()
    scenario_name = item["scenario"]
    task_id = item["task_id"]
    task = item["task"]
    db_schema = item["db_item"].get("db_schema", {})
    sample_str = _format_sample(item.get("sample_item"), config.sample_per_table)
    tasks = [task]   # 只为这一个任务规划/生成 API 规范

    # ① 规划 api_groups（只规划一次；规划失败重试规划，不进入生成循环）
    groups = []
    plan_errors = []
    diagnostic_dir = Path(config.output).parent / "_raw_llm" / safe_dir_name(f"{scenario_name}-{task_id}")
    for agent in agents.values():
        agent.label = f"{scenario_name}/{task_id}"
    for attempt in range(config.plan_retry + 1):
        check_cancelled()
        try:
            groups = _plan_groups(
                scenario_name, tasks, db_schema, sample_str, agents,
                feedback=plan_errors[-1] if plan_errors else "",
                diagnostic_dir=diagnostic_dir, attempt=attempt + 1,
            )
            if not groups:
                raise ValueError("planner 未返回有效分组")
        except Exception as e:
            message = f"{type(e).__name__}: {e}"
            plan_errors.append(message)
            logger.warning(f"{scenario_name}/{task_id}: planner 第 {attempt + 1}/{config.plan_retry + 1} 次失败：{message}")
            continue
        if groups:
            break
    if not groups:
        reason = "规划失败；" + "；".join(plan_errors) + f"；原始响应目录：{diagnostic_dir}"
        logger.error(f"{scenario_name}/{task_id}: {reason}")
        return {"scenario": scenario_name, "task_id": task_id, "task": task, "reason": reason}

    # ② 首轮生成全部 group
    api_groups = _generate_groups(scenario_name, tasks, db_schema, sample_str, groups, "", agents, orch)
    if not api_groups:
        logger.error(f"{scenario_name}/{task_id}: 生成的 API 规范为空，放弃该任务")
        return {"scenario": scenario_name, "task_id": task_id, "task": task, "reason": "生成的 API 规范为空"}

    last_problems = ""  # 最后一轮的失败原因（供 bad_tasks 记录真实原因）
    bad_names = set()
    retired_names = set()  # fixer 明确删除的冗余分组，不当作生成遗漏反复重建。
    if any(g.get("endpoints") for g in api_groups):
        retired_names.update(g["group_name"] for g in api_groups if g.get("endpoints") == [])
        api_groups = [g for g in api_groups if g.get("endpoints") != []]
    for attempt in range(config.max_retry + 1):
        check_cancelled()
        _normalize_spec_shapes(api_groups)
        # ③ 规则校验（确定性）：endpoint 结构合法
        _, dropped = validate_spec(api_groups)
        bad_names = {d["group"] for d in dropped}            # 规则不过的坏 group
        present = {g.get("group_name") for g in api_groups}  # 实际生成出来的 group
        missing_names = set(groups) - retired_names - present

        # files 由最终分组推导，绝不让 LLM 自由编排目录或修改绑定关系。
        # 规则失败时保留原始分组供 fixer 修复，不能把错误 endpoint 静默丢掉。
        # 先修复完整规范的结构，再做全局语义审查。不能让 reviewer 只看部分分组。
        verdicts = _review(
            api_groups, scenario_name, tasks, db_schema, sample_str, agents,
            max_retry=config.review_retry, diagnostic_dir=diagnostic_dir, round_number=attempt + 1,
        ) if not dropped and not missing_names else []
        # A reviewer transport/shape failure with no coverage payload gives fixer no
        # actionable evidence; keep the historical fail-fast behavior for that case.
        # Coverage payloads that merely contain malformed rows are normalized above and
        # proceed through the ordinary fixer path.
        if any(v.get("review_error") for v in verdicts):
            reason = verdicts[0].get("issue", "Reviewer output could not be validated.")
            reason += "；Reviewer 重试耗尽，未据此修改接口"
            logger.error(f"{scenario_name}/{task_id}: {reason}")
            return {"scenario": scenario_name, "task_id": task_id, "task": task, "reason": reason}
        for v in verdicts:
            if not v.get("pass"):
                bad_names.add(v.get("group"))

        # 全部通过 → 返回
        if not bad_names and not missing_names:
            result = build_spec_result(scenario_name, task_id, task, api_groups)
            coverage = [row for verdict in verdicts for row in verdict.get("coverage", [])]
            if coverage:
                result["task_coverage"] = coverage
            return result

        # 有坏 group / 缺失 group → 好 group 保留不动，坏 group 交给 fixer 定向修复，缺失 group 交给 designer 重新生成
        group_problems = _build_group_problems(verdicts, dropped)
        last_problems = "\n".join(f"group「{n}」: {p}" for n, p in group_problems.items()) or "坏 group 名对不上"
        if missing_names:
            last_problems += "\n缺失分组：" + "、".join(sorted(missing_names))
        # 达到上限就结束；不能在最后一轮修复后未经检查直接退出。
        if attempt == config.max_retry:
            break
        tasks_str = "\n".join(f"- {t}" for t in tasks)
        schema_str = format_db_schema(db_schema)
        # 修复有 endpoints 的坏 group（规则/评审不过的）；好 group 原样保留
        # 每次修复后立即更新全局状态，后续 fixer 看到的是最新规范。
        for name in list(g.get("group_name") for g in api_groups):
            if name not in bad_names:
                continue
            index = next(i for i, g in enumerate(api_groups) if g.get("group_name") == name)
            fixed = _fix_group(api_groups[index], scenario_name, tasks_str, schema_str, sample_str,
                               group_problems.get(name, ""), agents, api_groups=api_groups)
            problems_for_group = group_problems.get(name, "")
            if fixed.get("endpoints") == [] and _has_duplicate_issue(problems_for_group):
                fixed = _remove_cross_group_duplicates(api_groups[index], api_groups)
            if (fixed.get("endpoints") == []
                    and any(g.get("endpoints") for i, g in enumerate(api_groups) if i != index)
                    and (_allows_group_retirement(problems_for_group) or _has_duplicate_issue(problems_for_group))):
                api_groups.pop(index)
                retired_names.add(name)
                logger.info(f"{scenario_name}/{task_id}: 删除空分组 {name}，下一轮重新检查剩余规范的任务覆盖")
            else:
                # An empty fixer response without an explicit redundancy reason is a
                # failed fix, not permission to delete a group. Also never delete the
                # only non-empty group, otherwise the spec loses all executable ability.
                if fixed.get("endpoints") == []:
                    logger.warning(
                        f"{scenario_name}/{task_id}: 拒绝删除分组 {name}："
                        "fixer 返回空列表但评审没有确认该分组冗余"
                    )
                else:
                    api_groups[index] = fixed
        # 缺失 group（规划了但没生成）→ designer 重新生成（无 endpoints 可修，只能重生成）
        if missing_names:
            regenerated = _generate_groups(
                scenario_name, tasks, db_schema, sample_str,
                [name for name in groups if name in missing_names], "", agents, orch,
                planned_groups=groups, existing_groups=api_groups,
            )
            api_groups = api_groups + regenerated

    # 重试耗尽 → 放弃该任务，把最后一轮的真实失败原因记进 reason
    bad_list = "、".join(sorted(bad_names)) if bad_names else "(无)"
    reason = f"重试耗尽仍未通过校验；坏 group：{bad_list}"
    if last_problems:
        reason += f"；详情：{last_problems}"
    logger.error(f"{scenario_name}/{task_id}: {reason}")
    return {"scenario": scenario_name, "task_id": task_id, "task": task, "reason": reason}


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
    """把单个任务结果追加写一行到 output（JSONL 追加模式，中断/断网不丢已完成的）。"""
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def _load_resume_records(output_path: str) -> list:
    """新规则发现旧成功记录不合格时，备份原文件并只让这些任务重新生成。"""
    records = tools_jsonl_load(output_path)
    valid = []
    rejected = []
    for record in records:
        if (not isinstance(record.get("files"), list)
                or record.get("module_contract") != ENV_MODULE_CONTRACT):
            raise ValueError("旧 spec 缺少有效 files/module_contract；请使用 --resume false 重新生成")
        problems = validate_files(record.get("api_spec", {}), record["files"])
        if not problems:
            valid.append(record)
            continue
        _, dropped = validate_spec(record.get("api_spec", {}).get("api_groups"))
        if not dropped:
            raise ValueError(f"{record['scenario']}/{record['task_id']}: files 与规范不一致；请使用 --resume false 重新生成")
        rejected.append(record)
        logger.warning(f"{record['scenario']}/{record['task_id']}: 旧产物未通过当前规则，将重新生成：{dropped}")
    if rejected:
        source = Path(output_path)
        with tempfile.NamedTemporaryFile(dir=source.parent, prefix=source.stem + ".resume_backup_",
                                         suffix=".jsonl", delete=False) as backup:
            backup_path = backup.name
        shutil.copy2(source, backup_path)
        with tempfile.NamedTemporaryFile(dir=source.parent, prefix=source.stem + ".rewrite_",
                                         suffix=".tmp", delete=False) as temporary:
            temporary_path = temporary.name
        try:
            tools_jsonl_save(valid, temporary_path)
            os.replace(temporary_path, source)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        logger.info(f"续跑保留 {len(valid)} 条合格产物，重新生成 {len(rejected)} 条不合格产物；原文件备份：{backup_path}")
    return valid


def run(config: Config):
    """入口：读取任务+表结构+样本 → 展开成任务级 item → 断点续跑过滤 → 并发生成 API 规范 → 每完成一个任务增量落盘。"""
    logger.info("多 agent API 规范生成开始 ...")
    tasks_data = tools_jsonl_load(config.input_task)      # 任务文件（场景级 {scenario, tasks}）
    db_data = tools_jsonl_load(config.input_db)           # 表结构文件（任务级）
    sample_data = tools_jsonl_load(config.input_sample)   # 样本文件（任务级）
    db_map = {(d["scenario"], d.get("task_id", "")): d for d in db_data}
    sample_map = {(s["scenario"], s.get("task_id", "")): s for s in sample_data}
    kept = set(sample_map.keys())   # 第四阶段保留的任务

    if config.allowed_scenarios:
        tasks_data = [t for t in tasks_data if normalize_scenario_name(t["scenario"]) in config.allowed_scenarios]

    # 展开成任务级 item：每个任务独立生成一份 API 规范（只为这一个任务的初始状态）
    items = []
    for t in tasks_data:
        scenario_name = t["scenario"]
        for i, task in enumerate(t.get("tasks", []), start=1):
            task_id = f"task_{i}"
            key = (scenario_name, task_id)
            if key not in kept:
                logger.warning(f"{scenario_name}/{task_id}: 本任务在sample阶段未能成功生成sample，跳过")
                continue
            if key not in db_map:
                logger.warning(f"{scenario_name}/{task_id}: 在 db 文件中找不到对应表结构，跳过")
                continue
            items.append({"scenario": scenario_name, "task_id": task_id, "task": task,
                          "db_item": db_map[key], "sample_item": sample_map[key]})

    # 按 (scenario, task 自然序) 排序，保证后续取到的是确定性的顺序
    items.sort(key=_task_sort_key)

    # 准备输出目录
    out_dir = os.path.dirname(config.output) or "."
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.dirname(config.bad_output) or ".", exist_ok=True)

    # resume 默认 True：output 有产物 → 真断点续跑；无产物 → 全新生成
    done_keys = set()
    is_resuming = config.resume and os.path.exists(config.output)
    if is_resuming:
        try:
            done = _load_resume_records(config.output)
            done_keys = {(r["scenario"], r.get("task_id", "")) for r in done}
            logger.info(f"断点续跑：output 里已有 {len(done_keys)} 个任务，跳过")
        except Exception as e:
            raise ValueError(f"无法续跑 spec：{e}") from e
    elif os.path.exists(config.output):
        os.remove(config.output)   # 非续跑：清空旧的 output，重新开始
        logger.info("已关闭续跑：清空旧的 output，重新开始")
    elif config.resume:
        # 续跑开启但 output 无产物 → 全新生成
        logger.info("output 无产物，全新生成")

    if done_keys:
        items = [it for it in items if (it["scenario"], it["task_id"]) not in done_keys]
    # limit 截断放在这里：先跳过已生成的，再取前 limit 个「未生成」的新任务
    if config.limit is not None:
        items = items[: config.limit]

    # 输入与续跑清单校验成功后，才重建本次失败清单。
    if os.path.exists(config.bad_output):
        os.remove(config.bad_output)

    logger.info(f"待处理任务：{len(items)} 个（并发 {config.max_concurrency}）")
    if not items:
        logger.info("没有待处理任务，直接结束")
        return

    # 任务级并发：每个任务一个线程；每完成一个任务就增量落盘（追加一行）
    max_workers = max(1, min(config.max_concurrency, len(items)))
    done_count = 0
    pbar = tqdm(total=len(items), desc="生成 API 规范", unit="任务")
    with pbar, ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_item = {
            pool.submit(generate_spec_for_task, it, config): it
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
                    logger.success(f"✓ {r['scenario']}/{r['task_id']}: API 规范和文件清单已生成（{done_count}/{len(items)}）")
            else:
                logger.warning(f"✗ {item['scenario']}/{item['task_id']}: 放弃该任务（重试后仍未通过校验）")
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

