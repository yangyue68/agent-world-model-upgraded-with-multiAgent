"""多 agent 版本的环境代码生成（真 agent 多文件版，流水线第 6 步的升级版 env）。

流程：
  ① Architect(规划文件结构) → ② 规则校验(确定性，含 main.py)
  → ③ CodeGenerator(生成多文件代码包) → ④ 确定性补丁与静态校验
  → ⑤ 导入检查 + 多文件启动测试 + endpoint 冒烟测试 → ⑥ Fixer 局部修复 → 从静态校验重新检查
  → ⑦ 真实 rollout 探针通过后保存环境与轨迹，供 verifier 判断任务完成情况

成功标准：服务器能启动 + 规则校验通过 + endpoint 无 5xx。

任务级改造：每个任务独立生成一份 FastAPI 代码包（对齐本任务的 db_schema/api_spec/.db），
总文件 output/env/gen_envs_ma.jsonl 每行一个任务（该文件本身即充当合格名单，不再单独产出 pass 清单）。

输出：
  - gen_envs_ma.jsonl：合格任务环境清单（scenario/task_id/task/db_path/env_dir/traj_dir）
  - output/env/envs/<场景名>_<task_id>/：本任务的 FastAPI 代码（main.py/models.py/schemas.py/routers/ 等）
  - output/env/trajectory/<场景名>_<task_id>/：本任务的轨迹（traj.json + initial.db/final.db）

运行方式（在项目根目录 ma_upgrade/ 下）：
python -m stages.env.generate
"""

import ast
import json
import os
import hashlib
import shutil
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

# ma_upgrade 是独立项目根目录，确保可以导入 lib / stages。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 默认路径用「绝对路径」，与 CWD 无关
_DEFAULT_OUTPUT = str(_PROJECT_ROOT / "output" / "env" / "gen_envs_ma.jsonl")
_DEFAULT_BAD = str(_PROJECT_ROOT / "output" / "env" / "env_generate_bad.jsonl")
_DEFAULT_DB_DIR = str(_PROJECT_ROOT / "output" / "db" / "databases")
_DEFAULT_SCENARIO = str(_PROJECT_ROOT / "output" / "scenario" / "gen_scenario_ma.jsonl")
_DEFAULT_TASK = str(_PROJECT_ROOT / "output" / "task" / "gen_tasks_ma.jsonl")
_DEFAULT_SAMPLE = str(_PROJECT_ROOT / "output" / "sample" / "gen_sample_ma.jsonl")
_DEFAULT_SPEC_=str(_PROJECT_ROOT / "output" / "spec" / "gen_spec_ma.jsonl")
_DEFAULT_DB_SCHEMA_=str(_PROJECT_ROOT / "output" / "db" / "gen_db_ma.jsonl")
# 环境代码包落地目录 + 轨迹（traj.json / initial.db / final.db）落地目录
_DEFAULT_ENVS_DIR = str(_PROJECT_ROOT / "output" / "env" / "envs")
_DEFAULT_TRAJ_DIR = str(_PROJECT_ROOT / "output" / "env" / "trajectory")

PYTHON_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"

from loguru import logger

from lib.interrupt import run_cli, check_cancelled
from lib.interrupt import InterruptibleThreadPoolExecutor as ThreadPoolExecutor, as_completed
from lib.gpt import GPTClient
from lib.tools import (
    tools_jsonl_load,
    normalize_scenario_name,
    format_db_schema,
    safe_dir_name,
)
from stages.env.llm import EnvAgent, parse_agent_output, decode_json_reply
from lib.rollout_runner import run_single_rollout
from stages.env.prompts import (
    ENV_ARCHITECT_SYSTEM_PROMPT,
    ENV_ARCHITECT_USER_PROMPT,
    ENV_CODEGEN_SYSTEM_PROMPT,
    ENV_CODEGEN_USER_PROMPT,
    ENV_FIX_SYSTEM_PROMPT,
    ENV_FIX_USER_PROMPT,
)
from stages.env.validators import validate_architect_output, validate_codegen_output, validate_model_columns
from stages.env.static_check import analyze_files, run_import_check
from stages.env.runner import prepare_files, test_multifile_server, write_files_to_dir
from stages.rl.env import InProcessEnv
from stages.rl.reward import extract_final_answer
from tqdm import tqdm


@dataclass
class Config:
    """环境代码生成的配置项。"""
    input_spec: str = _DEFAULT_SPEC_              # API 规范文件（gen_spec_ma.jsonl）
    input_db: str = _DEFAULT_DB_SCHEMA_           # 表结构文件（gen_db_ma.jsonl）
    input_scenario: str = _DEFAULT_SCENARIO      # 场景描述（gen_scenario_ma.jsonl）
    input_task: str = _DEFAULT_TASK              # 任务文件（gen_tasks_ma.jsonl）
    input_sample: str = _DEFAULT_SAMPLE          # 示例数据文件（gen_sample_ma.jsonl）
    output: str = _DEFAULT_OUTPUT                # 输出文件（完整多文件代码包）
    bad_output: str = _DEFAULT_BAD               # 坏任务清单（每次跑完记录失败的任务）
    database_dir: str = _DEFAULT_DB_DIR          # .db 文件存放目录（任务级 .db 已由 db 阶段生成，此处保留兼容）
    envs_dir: str = _DEFAULT_ENVS_DIR            # 环境代码包落地目录（output/env/envs）
    trajectory_dir: str = _DEFAULT_TRAJ_DIR      # 轨迹落地目录（output/env/trajectory）
    model: str = "your-llm-model-name"           # 模型名（占位，必须被覆盖）
    allowed_scenarios: list[str] | None = None   # 只处理这些场景
    limit: int | None = None                     # 跳过已完成任务后，只处理前 limit 个任务
    max_retry: int = 2                           # 整份代码包重新生成的次数（不含首次）
    max_fix_retry: int = 4                       # 每个规则/运行检查闭环内 fixer 的最大调用次数
    plan_retry: int = 2                          # Architect 规划重试次数
    max_tokens: int = 50000                      # 单次输出 token 上限，与日志中的字符数不同
    max_concurrency: int = 12                   # 任务级并发数
    resume: bool = True                           # 断点续跑（跳过 output 里已完成的场景）
    sample_per_table: int = 2                    # 从sample阶段里每张表抽样的示例数据条数，因为sample阶段每张表生成的示例数据可能较多，不必全部交给env的generator，所以只抽样部分
    # ---- 导入期检查（起服务器之前的秒级检查，替代大量「起 uvicorn 等它崩」）----
    import_check_timeout: float = 25.0           # 单次 import main 的超时（秒）
    startup_timeout: float = 30.0                # 服务器启动就绪等待时间
    smoke_timeout: float = 15.0                  # 冒烟测试单次请求超时
    smoke_retry: int = 1                         # 仅传输错误时，重建数据库副本复测的次数
    save_raw_responses: bool = True              # 保存原始输出和 token/结束原因诊断
    # ---- 跑真实轨迹检测环境问题（方案 B：把 verifier 的探针前移到 env 阶段）----
    rollout_count: int = 2                      # 每个任务跑几条真实轨迹检测环境问题
    max_probe_retry: int = 2                    # 环境问题修复最大轮次
    probe_temperature: float = 0.3              # rollout 采样温度（低温度保证确定性）
    probe_model: str | None = None              # rollout 模型（None = 跟随主模型，在pre_process里处理）
    max_steps: int = 18                         # rollout 步数
    max_new_tokens: int =   50000                # rollout期间单次生成最大 token
    llm_env_check: bool = True                  # 是否使用 LLM 兜底判断环境问题（不 5xx 但逻辑错的场景）
    verbose: bool = False                       # 是否打印 rollout 轨迹（每步 LLM 输出 + 工具调用 + 环境回复）
    # ---- 协议严格模式（探针 rollout 用）----
    # True：回复里必须出现 <tool_call>/<thinking>/<final_answer> 之一，否则当场驳回重发；
    #       这样「不带 DSML 字样、检测函数抓不到」的原生格式（裸 JSON、<invoke> 等）也不会被静默放过去。
    strict_format: bool = True
    # ---- 产物清扫 ----
    # True：只有进入 gen_envs_ma.jsonl 合格名单的任务允许在磁盘上留下产物，收尾时把其余目录删掉
    #       （防的是失败/中断/异常留下的「假产物」被下一阶段当成合格环境用）。
    clean_orphans: bool = True

    def pre_process(self):
        """开工前检查：补齐默认值、校验输入、解析模型名。"""
        if self.limit is None:
            self.limit = int(1e18)#这是一个很大的值，实际上就是相当于不做限制
        assert self.input_spec and os.path.exists(self.input_spec), "input_spec is required"
        assert self.input_db and os.path.exists(self.input_db), "input_db is required"
        assert self.input_scenario and os.path.exists(self.input_scenario), "input_scenario is required"
        assert self.input_task and os.path.exists(self.input_task), "input_task is required"
        assert self.input_sample and os.path.exists(self.input_sample), "input_sample is required"
        assert self.output.endswith(".jsonl"), "Output path .jsonl is required"
        if os.environ.get("AWM_SYN_OVERRIDE_MODEL"):
            self.model = os.environ.get("AWM_SYN_OVERRIDE_MODEL")
        assert self.model != "your-llm-model-name", "请设置模型名（--model 或 AWM_SYN_OVERRIDE_MODEL）"
        if self.probe_model is None:
            self.probe_model = self.model
        for name in ("max_retry", "max_fix_retry", "plan_retry", "max_probe_retry", "smoke_retry"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不能为负数")
        for name in ("import_check_timeout", "startup_timeout", "smoke_timeout", "max_tokens", "max_concurrency"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        if self.rollout_count <= 0:
            raise ValueError("rollout_count 必须大于 0，env 合格产物必须有轨迹")



def build_agents(config: Config, label: str | None = None) -> dict:
    """构建 3 个 agent：架构规划 / 代码生成 / 局部修复。label 为场景名，日志里带上以区分。"""
    diagnostics_dir = str(Path(config.output).resolve().parent / "_raw_llm") if config.save_raw_responses else None
    return {
        "architect": EnvAgent(
            "architect", ENV_ARCHITECT_SYSTEM_PROMPT,
            client=GPTClient(timeout=10 * 60),
            model=config.model, temperature=0.3, max_tokens=config.max_tokens, label=label, diagnostics_dir=diagnostics_dir,
        ),
        "code_generator": EnvAgent(
            "code_generator", ENV_CODEGEN_SYSTEM_PROMPT,
            client=GPTClient(timeout=30 * 60),
            model=config.model, temperature=0.3, max_tokens=config.max_tokens, label=label, diagnostics_dir=diagnostics_dir,
        ),
        "fixer": EnvAgent(
            "fixer", ENV_FIX_SYSTEM_PROMPT,
            client=GPTClient(timeout=30 * 60),
            model=config.model, temperature=0.0, max_tokens=config.max_tokens, label=label, diagnostics_dir=diagnostics_dir,
        ),
    }


def _format_scenario(scenario_item: dict) -> str:
    """把场景条目格式化成一段业务描述文本。"""
    parts = [f"Scenario: {scenario_item.get('name', '')}"]
    desc = scenario_item.get("description", "")
    if desc:
        parts.append(f"Description: {desc}")
    sim = scenario_item.get("simulatable_features", [])
    if sim:
        parts.append("Simulatable features (implement these): " + "; ".join(sim))
    nonsim = scenario_item.get("non_simulatable_features", [])
    if nonsim:
        parts.append("Non-simulatable features (do NOT implement): " + "; ".join(nonsim))
    return "\n".join(parts)


def _format_task(task: str) -> str:
    """把单个任务格式化成文本（任务级：只为这一个任务生成代码）。"""
    return f"- {task}"


def _sample_data(sample_item: dict, per_table: int = 2) -> str:
    """从示例数据里每张表抽样最多 per_table 条 insert，格式化成文本。"""
    lines = []
    tables = sample_item.get("sample_data", {}).get("tables", [])
    for t in tables:
        table_name = t.get("table_name", "")
        inserts = t.get("insert_statements", []) or []
        picked = inserts[:per_table]
        if not picked:
            continue
        lines.append(f"-- table: {table_name} --")
        lines.extend(picked)
    return "\n".join(lines)


def _plan_files(scenario_name, scenario_desc, tasks_str, sample_str, api_spec, db_schema, agents, config):
    """① Architect 规划文件结构（带规则校验 + 重试）。返回 [{path, purpose}] 或 None。"""
    prompt = ENV_ARCHITECT_USER_PROMPT.format(
        scenario_name=scenario_name,
        scenario_description=scenario_desc,
        tasks=tasks_str,
        sample_data=sample_str,
        api_spec=json.dumps(api_spec, ensure_ascii=False),
        database_schema=format_db_schema(db_schema),
    )
    for plan_iter in range(config.plan_retry + 1):
        check_cancelled()
        raw = agents["architect"].run(prompt)
        data = parse_agent_output(agents["architect"], raw, scenario_name)
        problems = validate_architect_output(data)
        if not problems:
            return data["files"]
        logger.warning(f"{scenario_name}: Architect 规划校验未通过（第 {plan_iter + 1} 轮）: {'; '.join(problems)}")
        if plan_iter < config.plan_retry:
            # 把「上一轮产物 + 校验问题」一起拼回 prompt，让 Architect 在上一轮基础上修正（而不是只给问题、对不上上一轮 files）
            prev_str = json.dumps(data, ensure_ascii=False, indent=2)
            prompt += (
                "\n\n【上一次规划校验未通过】\n"
                f"上一次输出：\n{prev_str}\n"
                "校验问题：\n" + "; ".join(problems) + "\n"
                "请基于上一次输出修正这些问题，重新输出完整的 files JSON。"
            )
    return None


def _rule_check(files, db_schema) -> list[str]:
    """一处规则校验：格式/语法 + model 列名 + 跨文件符号一致性。

    跨文件检查（static_check.analyze_files）是纯 AST 的，零成本、不调 LLM，能在起服务器
    之前抓到 NameError / ModuleNotFoundError / ImportError / ORM 类当 response_model 这类
    「服务器一起来就崩」的错误。
    """
    problems = validate_codegen_output({"files": files}, require_main=True)
    if validate_codegen_output({"files": files}, check_syntax=False):
        return problems
    if not any(f["path"].strip() == "database.py" for f in files):
        problems.append("缺少 database.py：训练运行时必须从该模块导入 Base，请补齐 engine / SessionLocal / Base")
    problems.extend(validate_model_columns(files, db_schema))
    problems.extend(analyze_files(files))
    return problems


def _fingerprint(files) -> str:
    """代码包内容指纹，用来判断 fixer 是否真的改了东西（防空转）。"""
    payload = json.dumps(
        sorted((f.get("path", "").strip(), (f.get("content", "") or "").replace("\r\n", "\n"))
               for f in files or []), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _generate_files(scenario_name, scenario_desc, tasks_str, sample_str, api_spec, db_schema,
                    file_plan, feedback, agents, config):
    """③ CodeGenerator 生成多文件代码 → 规则校验 → fixer 修复闭环。

    生产归生产、修复归修复：generator 只负责首轮生成，规则校验（格式 + model 列名）不过就交给
    fixer 定向修复，修复次数用光仍不过才返回 None。返回 [{path, content}] 或 None。
    """
    plan_str = json.dumps({"files": file_plan}, ensure_ascii=False, indent=2)
    prompt = ENV_CODEGEN_USER_PROMPT.format(
        scenario_name=scenario_name,
        scenario_description=scenario_desc,
        tasks=tasks_str,
        sample_data=sample_str,
        api_spec=json.dumps(api_spec, ensure_ascii=False),
        database_schema=format_db_schema(db_schema),
        file_plan=plan_str,
        PYTHON_VERSION=PYTHON_VERSION,
        feedback=feedback,
    )
    raw = agents["code_generator"].run(prompt)
    data = parse_agent_output(agents["code_generator"], raw, scenario_name)
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list) or not files:
        logger.warning(f"{scenario_name}: 生成器输出无 files，无法交给 fixer")
        if not getattr(agents["code_generator"], "last_validation_error", ""):
            agents["code_generator"].last_validation_error = "生成器未返回有效 files 数组"
        return None
    shape_problems = validate_codegen_output({"files": files}, check_syntax=False)
    if shape_problems:
        agents["code_generator"].last_validation_error = "\n".join(shape_problems)
        logger.warning(f"{scenario_name}: 代码包格式错误：{'; '.join(shape_problems)}")
        return None

    # 有 files：把格式问题 + model 列名问题统一交给 fixer 定向修复，直到通过或修复次数用光
    current = prepare_files(files)
    seen_states = {_fingerprint(current)}
    for fix_iter in range(config.max_fix_retry + 1):
        check_cancelled()
        problems = _rule_check(current, db_schema)
        if not problems:
            return current
        agents["code_generator"].last_validation_error = "\n".join(problems)
        if fix_iter >= config.max_fix_retry:
            logger.warning(f"{scenario_name}: 规则校验仍不过（fix 第 {fix_iter + 1} 轮）：{'; '.join(problems)}")
            return None
        logger.warning(f"{scenario_name}: 规则校验不过（fix 第 {fix_iter + 1} 轮）：{'; '.join(problems)}")
        fixed = _fix_files(current, "\n".join(problems), "", scenario_desc, tasks_str, sample_str,
                           api_spec, db_schema, agents, scenario_name)
        if fixed is None:
            return None
        candidate = _merge_repair(current, fixed, seen_states, scenario_name)
        if candidate is None:
            return None
        current = candidate
    return None


def _fix_files(files, error_output, smoke_detail, scenario_desc, tasks_str, sample_str,
               api_spec, db_schema, agents, scenario_name):
    """⑥ Fixer 局部修复。返回 [{path, content}]（只含修改的文件）或 None。"""
    # 让 fixer 直接读源码和行号，避免先解读整包 JSON 的转义层。
    # 报错文件放在前面；输入中的代码围栏只用于阅读，输出仍是 JSON。
    errors = (error_output or "") + "\n" + (smoke_detail or "")
    ordered_files = sorted(files, key=lambda f: (f["path"] not in errors, f["path"]))
    sections = []
    for entry in ordered_files:
        numbered = "\n".join(f"{i:4}: {line}" for i, line in enumerate(entry["content"].splitlines(), 1))
        sections.append(f"### {entry['path']}\n```python\n{numbered}\n```")
    current_str = "\n\n".join(sections)
    prompt = ENV_FIX_USER_PROMPT.format(
        scenario_description=scenario_desc,
        tasks=tasks_str,
        sample_data=sample_str,
        api_spec=json.dumps(api_spec, ensure_ascii=False),
        database_schema=format_db_schema(db_schema),
        error_output=error_output or "",
        smoke_detail=("## Endpoint Smoke Test Errors\n" + smoke_detail) if smoke_detail else "",
        current_files=current_str,
    )
    raw = agents["fixer"].run(prompt)
    data = parse_agent_output(agents["fixer"], raw, scenario_name)
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        logger.warning(f"{scenario_name}: fixer 输出无法解析为 {{files:[...]}}")
        return None
    if data["files"]:
        problems = validate_codegen_output(data, require_main=False)
        if problems:
            logger.warning(f"{scenario_name}: fixer 补丁非法：{'; '.join(problems)}")
            return None
    return data["files"]


def _merge_repair(files, fixed, seen_states, scenario_name):
    """合并并规范化补丁；拒绝原样返回、只改注释和回到旧版本的修复。"""
    if not fixed:
        logger.warning(f"{scenario_name}: fixer 返回空补丁，结束本轮")
        return None
    candidate = prepare_files(_apply_fix(files, fixed))
    fingerprint = _fingerprint(candidate)
    def semantic_content(package):
        items = []
        for entry in package:
            try:
                content = ast.dump(ast.parse(entry["content"]), include_attributes=False)
            except SyntaxError:
                content = entry["content"]
            items.append((entry["path"], content))
        return sorted(items)

    if fingerprint in seen_states or semantic_content(candidate) == semantic_content(files):
        logger.warning(f"{scenario_name}: fixer 没有实质改动或回到了旧版本，结束本轮")
        return None
    seen_states.add(fingerprint)
    return candidate


def _apply_fix(files, fixed_files):
    """用 fixed_files 覆盖 files 里同 path 的文件，返回合并后的完整 files（其他文件不动）。"""
    file_map = {f["path"].strip(): f["content"] for f in files}
    for f in fixed_files:
        file_map[f["path"].strip()] = f["content"]
    return [{"path": p, "content": c} for p, c in file_map.items()]


def _persist_files(scenario_name, task_id, files, config):
    """把合格任务的多文件代码持久化落地到 output/env/envs/<safe_scenario>_<task_id>/。

    每个任务一个独立目录（safe_dir_name 命名，唯一、无特殊字符），
    目录下保持多文件的相对层级（routers/ 等子目录由 write_files_to_dir 的 os.makedirs 自动创建）。
    返回落地目录的绝对路径。
    """
    env_dir = os.path.join(config.envs_dir, f"{safe_dir_name(scenario_name)}_{task_id}")
    write_files_to_dir(files, env_dir, patch_db=True)
    return env_dir


def _validate_test_fix(files, scenario_name, scenario_desc, tasks_str, sample_str,
                       api_spec, db_schema, db_path, agents, config):
    """⑤⑥ 落盘 + 启动测试 + 冒烟测试 + fixer 局部修复的统一闭环。

    三级递进检查，每级都比上一级贵：
      1. 跨文件静态检查（每次 fixer 合并后重新检查）；
      2. 导入期检查（子进程只 import main，秒级）—— 抓 NameError / ModuleNotFoundError /
         ImportError / FastAPI 装饰器期报错，不用等端口、不用 HTTP 探测；
      3. 启动 + 冒烟测试（起 uvicorn，占端口，最贵）—— 只查「能启动但一调就 5xx」的问题。

    返回 (最终 files, {"success": bool, "feedback": str})。
    """
    current = prepare_files(files)
    seen_states = {_fingerprint(current)}
    for fix_iter in range(config.max_fix_retry + 1):
        check_cancelled()

        rule_problems = _rule_check(current, db_schema)
        if rule_problems:
            if fix_iter >= config.max_fix_retry:
                return current, {"success": False, "feedback": "\n\n【静态检查未通过】\n" + "\n".join(rule_problems)}
            fixed = _fix_files(current, "\n".join(rule_problems), "", scenario_desc, tasks_str, sample_str,
                               api_spec, db_schema, agents, scenario_name)
            candidate = _merge_repair(current, fixed, seen_states, scenario_name) if fixed else None
            if candidate is None:
                return current, {"success": False, "feedback": "\n\n【静态修复未产生有效改动】\n" + "\n".join(rule_problems)}
            current = candidate
            continue

        # ---- 第 2 级：导入期检查（起服务器之前）----
        # 确定性补丁已修掉常见机械错误；剩下的跨文件名字对不上、类没定义、ORM 类当
        # response_model 交给 fixer，报错信息比 uvicorn traceback 精确得多，也不占端口。
        import_ok, import_output = run_import_check(current, db_path, timeout=config.import_check_timeout)
        if not import_ok:
            if fix_iter >= config.max_fix_retry:
                return current, {"success": False, "feedback": '\n\n' + "【导入期报错，请重新生成】" + '\n' + (import_output or "")}
            logger.warning(f"{scenario_name}: 导入期报错（fix 第 {fix_iter + 1} 轮），交给 fixer 局部修复")
            fixed = _fix_files(current, import_output, "", scenario_desc, tasks_str, sample_str,
                              api_spec, db_schema, agents, scenario_name)
            if fixed is None:
                return current, {"success": False, "feedback": '\n\n' + "【fixer 修复失败，请重新生成】"}
            candidate = _merge_repair(current, fixed, seen_states, scenario_name)
            if candidate is None:
                return current, {"success": False, "feedback": "\n\n【导入期修复未产生有效改动】\n" + (import_output or "")}
            current = candidate
            continue

        # - started: 服务器是否启动成功
        # - output: 启动失败时的报错输出（成功时为空字符串）
        # - smoke_errors: 启动成功后的endpoint 5xx列表（空列表表示无5xx）
        started, output, smoke_errors = test_multifile_server(
            current, db_path, scenario_name, fix_iter,
            startup_timeout=config.startup_timeout,
            smoke_timeout=config.smoke_timeout,
            smoke_retry=config.smoke_retry,
        )

        #启动失败
        if not started:
            if fix_iter >= config.max_fix_retry:
                return current, {"success": False, "feedback": "\n\n【服务器启动报错，请重新生成】\n" + (output or "")}
            logger.warning(f"{scenario_name}: 启动失败（fix 第 {fix_iter + 1} 轮），交给 fixer 局部修复")
            fixed = _fix_files(current, output, "", scenario_desc, tasks_str, sample_str, api_spec, db_schema, agents, scenario_name)
            if fixed is None:
                return current, {"success": False, "feedback": "\n\n【fixer 修复失败，请重新生成】"}
            candidate = _merge_repair(current, fixed, seen_states, scenario_name)
            if candidate is None:
                return current, {"success": False, "feedback": "\n\n【启动修复未产生有效改动】\n" + (output or "")}
            current = candidate
            continue

        if smoke_errors:
            if fix_iter >= config.max_fix_retry:
                return current, {"success": False, "feedback": "\n\n【endpoint 返回 5xx，请重新生成】\n" + "\n".join(smoke_errors)}
            logger.warning(f"{scenario_name}: endpoint 5xx（fix 第 {fix_iter + 1} 轮）: {'; '.join(smoke_errors[:3])}")
            fixed = _fix_files(current, "", "\n".join(smoke_errors), scenario_desc, tasks_str, sample_str, api_spec, db_schema, agents, scenario_name)
            if fixed is None:
                return current, {"success": False, "feedback": "\n\n【fixer 修复失败，请重新生成】"}
            candidate = _merge_repair(current, fixed, seen_states, scenario_name)
            if candidate is None:
                return current, {"success": False, "feedback": "\n\n【冒烟修复未产生有效改动】\n" + "\n".join(smoke_errors)}
            current = candidate
            continue

        return current, {"success": True}

    return current, {"success": False, "feedback": ""}


def _traj_to_text(traj: dict) -> str:
    """把轨迹转成可读文本（供 LLM 环境判断），system 省略、长回复截断。"""
    lines = []
    for msg in traj.get("messages", []):
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            continue
        if role == "assistant":
            lines.append(f"[AGENT]\n{content[:800]}")
        elif role == "user":
            lines.append(f"[ENV]\n{content[:800]}")
    return "\n\n".join(lines)


def _save_traj_json(run_dir: str, traj: dict) -> str:
    """把单条轨迹写到 run_dir/traj.json（db 保留在 run_dir，不删，供 verifier 复用）。"""
    data = {
        "messages": traj.get("messages", []),
        "tool_call_names": traj.get("tool_call_names", []),
        "error_details": traj.get("error_details", []),
        "final_answer": extract_final_answer(traj),
        # 模型违反 <tool_call> 协议（用了 DSML 等原生格式）的次数，以及实际发出的纠错消息数：
        # 次数>0 说明这条轨迹里有过「被驳回、没发给 env」的工具调用，供人工/上层判断探针是否有效。
        "format_violations": traj.get("format_violations", 0),
        "format_resends": traj.get("format_resends", 0),
    }
    path = os.path.join(run_dir, "traj.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def _parse_env_judgment(raw: str) -> list[str]:
    """只接受有明确肯定结论和轨迹证据的 bug，格式偏离不等于环境有错。"""
    normalized = raw.strip().strip("`\"'。.!！ ").lower()
    if normalized in {"ok", "环境代码没有问题", "环境没有问题", "没有发现环境代码问题", "no issues"}:
        return []
    try:
        data = decode_json_reply(raw)
    except (ValueError, TypeError):
        logger.warning(f"[env] 环境质检回复无法解析，保留确定性检查结果：{raw[:300]}")
        return []
    if not isinstance(data, dict) or type(data.get("has_env_bug")) is not bool:
        logger.warning("[env] 环境质检缺少布尔 has_env_bug，保留确定性检查结果")
        return []
    if not data["has_env_bug"]:
        return []
    issues = data.get("issues")
    if not isinstance(issues, list):
        return []
    return [f"{issue['description']}；轨迹证据：{issue['evidence']}"
            for issue in issues if isinstance(issue, dict)
            and isinstance(issue.get("description"), str) and issue["description"].strip()
            and isinstance(issue.get("evidence"), str) and issue["evidence"].strip()]


def _llm_judge_env(traj_dir: str, task: str, client, model, rollout_count: int) -> list[str]:
    """LLM 兜底：读轨迹判断环境代码是否有问题（不 5xx 但逻辑错的场景）。返回问题列表。"""
    texts = []
    for i in range(rollout_count):
        check_cancelled()
        p = os.path.join(traj_dir, f"probe_{i}", "traj.json")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    traj = json.load(f)
                texts.append(_traj_to_text(traj))
            except Exception:
                continue
    if not texts:
        return []
    prompt = (
        "你是环境代码质检员。下面是一个 agent 在某个 FastAPI 环境里执行任务的多条轨迹。"
        "请判断这些轨迹是否暴露了【环境代码的 bug】（例如：接口返回空数据但应该有条目、字段名错乱、"
        "查询/过滤逻辑错误、数据不一致等）。不要把 agent 能力不足、参数错误或任务未完成当成环境 bug。"
        "只根据轨迹中的明确证据判断，不知道数据库里应该有什么数据时，不推测空结果是 bug。\n\n"
        f"任务：{task}\n\n轨迹：\n" + "\n\n---\n\n".join(texts)[:6000] + "\n\n"
        '只返回 JSON：{"has_env_bug": false, "issues": []}。'
        '有确切 bug 时返回 {"has_env_bug": true, "issues": '
        '[{"description": "问题描述", "evidence": "引用轨迹中的具体工具调用和返回"}]}。'
    )
    try:
        resp = client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1000,
            model=model or "your-llm-model-name",
        ) or ""
    except Exception as e:
        logger.warning(f"[env] LLM 环境判断失败：{e}")
        return []
    return _parse_env_judgment(resp)


def _rmtree_retry(path: str, reason: str = "") -> bool:
    """删除目录（Windows 上 .db 可能被杀软/残留句柄短暂占用，所以重试几次）。

    返回是否删除成功（目录本来就不存在也算成功）。删不掉只告警、不抛异常，
    因为清理失败不该再让一个已经失败的任务把整个 run 打崩。
    """
    if not os.path.isdir(path):
        return True
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            logger.info(f"[env] 已清理{('（' + reason + '）') if reason else ''}：{path}")
            return True
        except OSError as e:
            if attempt == 2:
                logger.warning(f"[env] 清理失败（{reason or '未合格产物'}）：{path} -> {e}")
                return False
            time.sleep(0.5 * (attempt + 1))  # 清理本身不再抛取消，给 finally 完成的机会。
    return False


def _cleanup_task_artifacts(scenario_name: str, task_id: str, config: Config, reason: str = "") -> None:
    """清掉某个任务在 envs/ 与 trajectory/ 下的全部产物。

    只有最终进入 gen_envs_ma.jsonl 合格名单的任务才允许留下产物。失败任务（含异常中断）如果不清，
    下一阶段就会看到「不在合格名单里、但目录存在」的假环境/假轨迹 —— 本次实测就是这样：
    3 个任务只有 1 个合格，却落地了 3 份 env 代码 + 3 份 trajectory。
    """
    stamp = f"{safe_dir_name(scenario_name)}_{task_id}"
    for base in (config.envs_dir, config.trajectory_dir):
        _rmtree_retry(os.path.join(base, stamp), reason=reason)


# 同一场景的不同任务 env 代码不同，而 env 模块名（main/database/models）在进程内是全局的，
# 所以同一场景的探针必须串行，避免 A 任务载入的模块被 B 任务清掉（不同场景之间仍可并发）。
_probe_locks: dict[str, threading.Lock] = {}
_probe_locks_guard = threading.Lock()


def _probe_lock(scenario_name: str) -> threading.Lock:
    """取某个场景的探针锁（懒创建，进程内唯一）。"""
    with _probe_locks_guard:
        lock = _probe_locks.get(scenario_name)
        if lock is None:
            lock = threading.Lock()
            _probe_locks[scenario_name] = lock
        return lock


def _rollout_check(env_dir: str, task: str, db_schema_path: str, sample_path: str,
                   task_id: str, scenario_name: str, config: Config, agents) -> tuple[list[str], str | None]:
    """跑 rollout_count 条真实轨迹，检测环境问题（确定性 + LLM 兜底）。

    返回 (problems, traj_dir)：
      - problems 非空：环境有问题（轨迹目录已清理）
      - problems 为空：环境没问题，traj_dir 是保留轨迹的目录

    两条硬约束（本次实测踩过坑）：
      1. 同一场景的探针必须串行 —— 同场景不同任务的 env 代码不同，进程内 env 模块名却是全局的，
         并发起来会互相清模块，导致探针跑的是别的任务的代码（实测报错栈里出现 ..._task_1/routers/users.py）；
      2. 探针自己崩了（建库被文件占用、载入 env 失败等）不能把异常抛给上层 —— 抛出去会逃逸到线程池、
         留下没人清理的产物；这里转成 problems，走正常的「修复 / 淘汰」流程。
    """
    client = agents["fixer"].client
    model = config.probe_model
    try:
        task_idx = int(str(task_id).split("_", 1)[1]) - 1
    except (ValueError, IndexError):
        task_idx = None
    traj_dir = os.path.join(config.trajectory_dir, f"{safe_dir_name(scenario_name)}_{task_id}")

    problems: list[str] = []
    with _probe_lock(scenario_name):
        os.makedirs(traj_dir, exist_ok=True)
        for i in range(config.rollout_count):
            check_cancelled()
            run_dir = os.path.join(traj_dir, f"probe_{i}")
            # 先清掉上一轮 probe 残留的 run_dir（含被占着的 final.db），避免落库时 os.replace 撞上旧文件
            _rmtree_retry(run_dir, reason=f"{scenario_name}/{task_id} probe_{i} 上一轮残留")
            os.makedirs(run_dir, exist_ok=True)
            env = InProcessEnv(scenario_name, env_dir, db_schema_path, sample_path, run_dir, task_id=task_idx)
            try:
                env.start()
                if not env.list_tools():
                    raise ValueError("环境没有可调用工具：请检查路由是否注册及 OpenAPI operationId 映射")
                traj = run_single_rollout(client, task, env, config.max_steps, config.probe_temperature,
                                          config.max_new_tokens, config.verbose, model,
                                          strict_format=config.strict_format)
                if traj.get("has_5xx"):
                    for tool, err in traj.get("error_details", []) or []:
                        problems.append(f"工具 {tool} 触发 5xx：{err}")
                if traj.get("format_violations"):
                    # 模型没用约定协议（DSML 原生格式 / 严格模式下没有合法标签）→ lib/rollout_runner 已把那次
                    # 调用驳回、没发给 env，并回了纠错消息。这是模型格式问题，不是环境代码 bug，
                    # 所以只告警、不写进 problems（不据此判环境不合格）。
                    logger.warning(
                        f"{scenario_name}/{task_id}: probe_{i} 命中 {traj['format_violations']} 次协议违规"
                        f"（已驳回、未发给 env；发出 {traj.get('format_resends', 0)} 条纠错消息）"
                    )
                _save_traj_json(run_dir, traj)
            except Exception as e:
                tb = traceback.format_exc()
                logger.warning(f"{scenario_name}/{task_id}: probe_{i} 运行异常：{e}\n{tb}")
                problems.append(f"probe_{i} 运行异常：{type(e).__name__}: {e}")
            finally:
                env.close()
            if problems:
                shutil.rmtree(traj_dir, ignore_errors=True)
                return problems, None

    if config.llm_env_check and not problems:
        problems.extend(_llm_judge_env(traj_dir, task, client, model, config.rollout_count))
    if problems:
        shutil.rmtree(traj_dir, ignore_errors=True)
        return problems, None
    return [], traj_dir


def generate_env_for_task(item: dict, config: Config):
    """单个任务的对外入口：跑生成流程，并保证「失败 / 异常时磁盘上不留任何产物」。

    返回：成功 = 合格记录（含 env_dir/traj_dir）；失败 = {"reason": ...}（写进 env_generate_bad.jsonl）。
    """
    scenario_name = item["scenario"]
    task_id = item["task_id"]
    try:
        result = _try_generate_env_for_task(item, config)
    except Exception as e:
        logger.error(f"{scenario_name}/{task_id}: 生成过程异常 {e}\n{traceback.format_exc()}")
        _cleanup_task_artifacts(scenario_name, task_id, config, reason="任务异常")
        return {"scenario": scenario_name, "task_id": task_id, "task": item["task"],
                "reason": f"异常: {e}"}
    if "reason" in result:
        # 不合格任务：env 代码包 / 轨迹一律清掉，只有进了 gen_envs_ma.jsonl 的任务才允许留产物
        _cleanup_task_artifacts(scenario_name, task_id, config, reason="任务未通过")
    return result


def _try_generate_env_for_task(item: dict, config: Config):
    """对单个任务：Architect 规划 → CodeGenerator 生成多文件 → 启动测试 + fixer 局部修复。"""
    scenario_name = item["scenario"]
    task_id = item["task_id"]
    task = item["task"]
    # 开工前先清掉本任务可能存在的残留产物（上一次失败/中断留下的），避免新旧混在一起被判成合格
    _cleanup_task_artifacts(scenario_name, task_id, config, reason="重跑前的旧产物")
    agents = build_agents(config, label=f"{scenario_name}/{task_id}")
    api_spec = item["spec_item"].get("api_spec", {})
    db_schema = item["db_item"].get("db_schema", {})

    # .db 直接复用 db 阶段生成的任务级 .db（相对项目根路径），转绝对路径供启动测试
    rel_db_path = item["db_item"].get("db_path", "")
    abs_db_path = rel_db_path if os.path.isabs(rel_db_path) else os.path.join(str(_PROJECT_ROOT), rel_db_path)

    scenario_desc = _format_scenario(item["scenario_item"])
    tasks_str = _format_task(task)
    sample_str = _sample_data(item["sample_item"], config.sample_per_table)

    # ① Architect 规划文件结构
    file_plan = _plan_files(scenario_name, scenario_desc, tasks_str, sample_str,
                            api_spec, db_schema, agents, config)
    if file_plan is None:
        logger.error(f"{scenario_name}/{task_id}: Architect 规划失败，放弃该任务")
        return {"scenario": scenario_name, "task_id": task_id, "task": task, "reason": "Architect 规划失败"}

    feedback = ""
    last_problems = ""
    for attempt in range(config.max_retry + 1):
        check_cancelled()
        # ③ CodeGenerator 生成多文件（内部含规则校验 + fixer 修复闭环）
        logger.info(f"{scenario_name}/{task_id}: 第 {attempt + 1} 次生成代码包 ...")
        files = _generate_files(scenario_name, scenario_desc, tasks_str, sample_str,
                                api_spec, db_schema, file_plan, feedback, agents, config)
        if files is None:
            logger.warning(f"{scenario_name}/{task_id}: 生成 + 规则修复失败，放弃本轮")
            last_problems = getattr(agents["code_generator"], "last_validation_error", "") or "生成 + 规则修复失败"
            feedback = "\n\n【上一次生成或规则修复失败，请解决以下问题】\n" + last_problems
            continue

        # ⑤⑥ 启动测试 + 冒烟测试 + fixer 局部修复闭环
        files, state = _validate_test_fix(files, scenario_name, scenario_desc, tasks_str, sample_str,
                                          api_spec, db_schema, abs_db_path, agents, config)
        if state.get("success"):
            # 持久化落盘：把多文件代码写进 output/env/envs/<safe_scenario>_<task_id>/（后续阶段可直接调用，无需再解析 JSON）
            env_dir = _persist_files(scenario_name, task_id, files, config)
            rel_env_dir = os.path.relpath(env_dir, str(_PROJECT_ROOT)).replace("\\", "/")

            # 方案 B：跑真实轨迹检测环境问题（no such column / 5xx / 隐性逻辑错），
            # 有问题就 fixer 就地修复；没问题就把轨迹保留到 trajectories/ 供 verifier 复用。
            traj_dir = None
            probe_ok = True
            probe_states = {_fingerprint(files)}
            for probe_iter in range(config.max_probe_retry + 1):
                check_cancelled()
                problems, traj_dir = _rollout_check(env_dir, task, config.input_db, config.input_sample,
                                                    task_id, scenario_name, config, agents)
                if not problems:
                    break
                logger.warning(f"{scenario_name}/{task_id}: 环境问题（probe 第 {probe_iter + 1} 轮）：{'；'.join(problems[:3])}")
                if probe_iter >= config.max_probe_retry:
                    probe_ok = False
                    last_problems = "环境问题未解决：" + "\n".join(problems)
                    break
                # fixer 修复环境问题，再重新启动测试 + 重新落盘
                fixed = _fix_files(files, "\n".join(problems), "", scenario_desc, tasks_str, sample_str,
                                   api_spec, db_schema, agents, scenario_name)
                if fixed is None:
                    probe_ok = False
                    last_problems = "fixer 修复环境问题失败"
                    break
                candidate = _merge_repair(files, fixed, probe_states, scenario_name)
                if candidate is None:
                    probe_ok = False
                    last_problems = "探针修复未产生有效改动：" + "\n".join(problems)
                    break
                files = candidate
                files, state2 = _validate_test_fix(files, scenario_name, scenario_desc, tasks_str, sample_str,
                                                   api_spec, db_schema, abs_db_path, agents, config)
                if not state2.get("success"):
                    probe_ok = False
                    last_problems = state2.get("feedback", "修复后启动测试失败")
                    break
                probe_states.add(_fingerprint(files))
                env_dir = _persist_files(scenario_name, task_id, files, config)
            if probe_ok:
                rel_traj_dir = os.path.relpath(traj_dir, str(_PROJECT_ROOT)).replace("\\", "/") if traj_dir else ""
                return {"scenario": scenario_name, "task_id": task_id, "task": task,
                        "db_path": rel_db_path, "env_dir": rel_env_dir, "traj_dir": rel_traj_dir}
            # 环境问题未解决 → 带反馈回到 attempt 循环重新生成
            feedback = "\n\n【环境问题，请重新生成】\n" + last_problems
            continue

        logger.warning(f"{scenario_name}/{task_id}: 第 {attempt + 1} 次的校验/修复未通过，带反馈重新生成")
        feedback = state.get("feedback", "\n\n【校验/测试失败，请重新生成】")
        last_problems = feedback

    reason = "重试耗尽仍未成功"
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


def _sweep_orphan_artifacts(config: Config) -> None:
    """清扫：删掉不在合格名单（output，即 gen_envs_ma.jsonl）里的 envs/ 与 trajectory/ 目录。

    约定：产物目录名 = <safe 场景名>_<task_id>。不在名单里的一律清掉，保证磁盘上留下的产物
    与合格名单严格一一对应 —— 这样「只落地成功任务」不依赖每个失败分支都记得删干净。
    """
    try:
        qualified = {f"{safe_dir_name(r['scenario'])}_{r.get('task_id', '')}"
                     for r in tools_jsonl_load(config.output)}
    except Exception as e:
        logger.warning(f"[env] 读取合格名单失败，跳过产物清扫：{e}")
        return
    removed = 0
    for base in (config.envs_dir, config.trajectory_dir):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name in qualified:
                continue
            if _rmtree_retry(os.path.join(base, name), reason="不在合格名单里"):
                removed += 1
    logger.info(f"[env] 产物清扫完成：合格名单 {len(qualified)} 个任务，清掉 {removed} 个未合格目录")


def run(config: Config):
    """入口：读取前五阶段产物 → 断点续跑过滤 → 并发生成多文件代码并启动测试 → 落盘。"""
    logger.info("多 agent 多文件环境代码生成开始 ...")
    spec_data = tools_jsonl_load(config.input_spec)          # 任务级 {scenario, task_id, task, api_spec}
    db_data = tools_jsonl_load(config.input_db)              # 任务级 {scenario, task_id, task, db_schema, db_path}
    scenario_data = tools_jsonl_load(config.input_scenario)  # 场景级 {name, description, ...}
    sample_data = tools_jsonl_load(config.input_sample)      # 任务级 {scenario, task_id, task, sample_data, ...}

    db_map = {(d["scenario"], d.get("task_id", "")): d for d in db_data}
    sample_map = {(s["scenario"], s.get("task_id", "")): s for s in sample_data}
    scenario_map = {normalize_scenario_name(s.get("name", "")): s for s in scenario_data}

    if config.allowed_scenarios:
        spec_data = [s for s in spec_data if normalize_scenario_name(s["scenario"]) in config.allowed_scenarios]

    # 展开成任务级 item：以第五阶段（spec）任务为准，只处理产物都齐全的任务
    items = []
    for s in spec_data:
        scenario_name = s["scenario"]
        task_id = s.get("task_id", "")
        key = (scenario_name, task_id)
        scenario_key = normalize_scenario_name(scenario_name)
        missing = []
        if scenario_key not in scenario_map:
            missing.append("scenario")
        if key not in db_map:
            missing.append("db")
        if key not in sample_map:
            missing.append("sample")
        if missing:
            logger.warning(f"{scenario_name}/{task_id}: 缺少产物 {missing}，跳过")
            continue
        items.append({
            "scenario": scenario_name,
            "task_id": task_id,
            "task": s.get("task", ""),
            "scenario_item": scenario_map[scenario_key],
            "db_item": db_map[key],
            "sample_item": sample_map[key],
            "spec_item": s,
        })

    # 按 (scenario, task 自然序) 排序，保证 --limit 取到的是确定性的前 N 个任务
    items.sort(key=_task_sort_key)

    out_dir = os.path.dirname(config.output) or "."
    os.makedirs(out_dir, exist_ok=True)

    # 每次跑完重新生成 bad_tasks 清单（覆盖旧的，只记录本次失败的任务）
    if os.path.exists(config.bad_output):
        os.remove(config.bad_output)

    # 断点续跑：resume 时跳过 output 里已完成的任务；非 resume 时清空旧 output 重新开始
    done_keys = set()
    if config.resume and os.path.exists(config.output):
        try:
            done = tools_jsonl_load(config.output)
            done_keys = {(r["scenario"], r.get("task_id", "")) for r in done}
            logger.info(f"断点续跑：output 里已有 {len(done_keys)} 个任务，跳过")
        except Exception as e:
            logger.warning(f"读取 output 失败，忽略断点续跑：{e}")
    elif not config.resume and os.path.exists(config.output):
        os.remove(config.output)
        logger.info("非续跑模式：清空旧的 output，重新开始")
    if done_keys:
        items = [it for it in items if (it["scenario"], it["task_id"]) not in done_keys]
    # limit 截断放在这里：先跳过已生成的，再取前 limit 个「未生成」的新任务
    if config.limit is not None:
        items = items[: config.limit]

    # 开工清扫：只有进入合格名单的任务允许在磁盘上留下产物（防的是上一轮失败/中断留下的假产物）
    if config.clean_orphans:
        _sweep_orphan_artifacts(config)

    logger.info(f"待处理任务：{len(items)} 个（并发 {config.max_concurrency}）")
    if not items:
        logger.info("没有待处理任务，直接结束")
        return

    max_workers = max(1, min(config.max_concurrency, len(items)))
    done_count = 0
    pbar = tqdm(total=len(items), desc="生成环境代码", unit="任务")
    with pbar, ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_item = {
            pool.submit(generate_env_for_task, it, config): it
            for it in items
        }
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                r = future.result()
            except Exception as e:
                logger.error(f"{item['scenario']}/{item['task_id']}: 生成过程异常 {e}")
                # 兜底清理：正常路径下 generate_env_for_task 内部已经清过，这里防的是它自己就崩了的情况
                _cleanup_task_artifacts(item["scenario"], item["task_id"], config, reason="任务异常")
                _append_result({"scenario": item["scenario"], "task_id": item["task_id"], "task": item["task"], "reason": f"异常: {e}"}, config.bad_output)
                pbar.update(1)
                continue
            if r is not None:
                if "reason" in r:   # 失败记录 → 写 bad_tasks 清单
                    _append_result(r, config.bad_output)
                    logger.warning(f"✗ {r['scenario']}/{r['task_id']}: 放弃该任务（{r['reason'][:80]}）")
                else:               # 成功 → 写 output（gen_envs_ma.jsonl 即充当合格清单）
                    _append_result(r, config.output)
                    done_count += 1
                    logger.success(f"✓ {r['scenario']}/{r['task_id']}: 环境代码已生成并启动测试 + 轨迹检测通过（{done_count}/{len(items)}）")
            else:
                logger.warning(f"✗ {item['scenario']}/{item['task_id']}: 放弃")
            pbar.update(1)
    logger.info(f"处理任务：{done_count}/{len(items)}，结果保存到 {config.output}")


def main():
    from simpleArgParser import parse_args
    config: Config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)




