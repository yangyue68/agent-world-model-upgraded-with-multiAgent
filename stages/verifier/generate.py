"""使用 LLM judge 评判 env 阶段保存的 rollout，并按任务汇总完成情况。

处理范围由 gen_envs_ma.jsonl 决定：只有已落地环境代码且保存了轨迹的任务参与评判。
每条 probe 读取 traj.json、initial.db 和 final.db，计算完整数据库 diff，然后把
「任务 + 轨迹 + 初始快照 + diff」交给 judge。final_answer 已在轨迹中，不单独重复传入。
数据库没有变化时传 diff=None；初始快照的大小淘汰只由 sample 阶段负责。

每个任务至少一条 probe 被 judge 判为 complete，就进入 solvable_tasks.jsonl；
全部 probe 已成功评判且均未完成，则进入 unsolvable_tasks.jsonl。请求或材料错误单独记录，
不能冒充「未完成」。名称 solvable/unsolvable 表示本次 probe 的表现，不表示任务理论可解性。

项目根目录是 ma_upgrade 本身。运行方式：
  python -m stages.verifier.generate --model <裁判模型名>
  python -m stages.verifier.generate --max_concurrency 5 --limit 10
模型也可通过 AWM_SYN_OVERRIDE_MODEL 设置。详细输入输出见同目录 README.md。
"""

import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from loguru import logger
from lib.interrupt import run_cli, check_cancelled
from lib.interrupt import InterruptibleThreadPoolExecutor as ThreadPoolExecutor, as_completed
from lib.db_diff import diff_snapshots, snapshot_db
from lib.gpt import GPTClient
from lib.llm_judge import LLM_JUDGE_SYSTEM_PROMPT, llm_judge
from lib.tools import normalize_scenario_name, safe_dir_name

_OUTPUT_DIR = _PROJECT_ROOT / "output" / "verifier"
_CLASSIFICATIONS = {"complete", "incomplete", "server_error", "agent_error"}
_MODE = "env_rollout_llm_judge_v1"
_OUTPUT_FIELDS = ("output", "bad_output", "ready_output", "solvable_output", "unsolvable_output")
_MUTATING_TASK_RE = re.compile(
    r"\b(create|add|update|change|set|delete|remove|cancel|void|assign|attach|configure|enable|disable|send|withdraw|record|bulk|mark|correct|save|require|edit|modify|insert|submit)\b"
    r"|新增|添加|更新|修改|删除|取消|作废|分配|设置|配置|启用|禁用|发送|撤回|记录|标记|保存|要求|提交",
    re.IGNORECASE,
)


def _project_path(raw: str) -> Path:
    """相对路径以 ma_upgrade 为根；兼容带 ma_upgrade/ 前缀的旧写法。"""
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0] == _PROJECT_ROOT.name:
        path = Path(*path.parts[1:])
    return (_PROJECT_ROOT / path).resolve()


def _portable_path(path: Path) -> str:
    """项目内路径保存为相对路径，打包或迁移项目时仍可使用。"""
    try:
        return path.resolve().relative_to(_PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


@dataclass
class Config:
    """env 轨迹评判的配置项。"""
    input_env: str = str(_PROJECT_ROOT / "output/environment/gen_envs_ma.jsonl")  # 决定处理范围的 environment 清单
    input_sample: str = str(_PROJECT_ROOT / "output/sample/gen_sample_ma.jsonl")  # 仅用于初始快照或任务文本兜底
    trajectory_root: str = str(_PROJECT_ROOT / "output/environment/trajectory")  # 旧 environment 记录没有 traj_dir 时兜底
    output: str = str(_OUTPUT_DIR / "gen_verifier_ma.jsonl")  # 每个任务的 judge 汇总与逐 probe 结果
    bad_output: str = str(_OUTPUT_DIR / "verifier_bad_tasks.jsonl")  # judge/材料处理错误，不是未完成清单
    ready_output: str = str(_OUTPUT_DIR / "rl_ready_tasks.jsonl")  # 已确定完成或未完成的全部任务
    solvable_output: str = str(_OUTPUT_DIR / "solvable_tasks.jsonl")  # 至少一个 probe 完成的任务
    unsolvable_output: str = str(_OUTPUT_DIR / "unsolvable_tasks.jsonl")  # 所有 probe 都未完成的任务
    evidence_dir: str = str(_OUTPUT_DIR / "judge_evidence")  # 保存 judge 输入、结果及续跑缓存
    model: str = "your-llm-model-name"  # 裁判模型；也可由 AWM_SYN_OVERRIDE_MODEL 提供
    provider: str = "azure"  # 使用项目 GPTClient，AWM_SYN_LLM_PROVIDER 可覆盖
    max_concurrency: int = 5  # 任务级并发；每个任务内逐条评判 probe
    max_retry: int = 2  # judge_error 的额外重试次数
    request_timeout: float = 600.0  # 单次 judge 请求超时秒数
    max_tokens: int = 4096  # judge 输出 token 上限，不截断输入证据
    resume: bool = True  # 复用输入内容和 judge 配置均未变化的成功评判
    allowed_scenarios: list[str] | None = None  # 可选场景过滤
    limit: int | None = None  # 只处理过滤后前 limit 个 env 任务

    def pre_process(self):
        """统一项目路径，检查输入、模型与运行参数。"""
        for name in ("input_env", "input_sample", "trajectory_root", "evidence_dir", *_OUTPUT_FIELDS):
            value = getattr(self, name)
            if value:
                setattr(self, name, str(_project_path(value)))
        if not self.input_env or not Path(self.input_env).is_file():
            raise FileNotFoundError(f"env 清单不存在：{self.input_env}")
        outputs = [Path(getattr(self, name)) for name in _OUTPUT_FIELDS]
        if any(path.suffix.lower() != ".jsonl" for path in outputs):
            raise ValueError("所有清单输出都必须是 .jsonl 文件")
        if len(set(outputs)) != len(outputs):
            raise ValueError("各输出清单必须使用不同路径")
        if any(str(path) in {self.input_env, self.input_sample} for path in outputs):
            raise ValueError("输出清单不能覆盖输入产物")
        if self.model == "your-llm-model-name":
            self.model = os.environ.get("AWM_SYN_OVERRIDE_MODEL", self.model)
        if not self.model or self.model == "your-llm-model-name":
            raise ValueError("请设置裁判模型（--model 或 AWM_SYN_OVERRIDE_MODEL）")
        if self.max_concurrency < 1 or self.max_retry < 0 or self.max_tokens < 1 or self.request_timeout <= 0:
            raise ValueError("并发、输出 token 和超时必须为正数，max_retry 不能为负数")
        if self.limit is not None and self.limit < 0:
            raise ValueError("limit 不能为负数")


def _load_jsonl(path: str) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as source:
        for line_no, line in enumerate(source, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_no} 不是 JSON 对象")
            records.append(record)
    return records


def _json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_json_text(value), encoding="utf-8")
    os.replace(temporary, path)


def _write_jsonl(path: str, records: list[dict]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as destination:
        for record in records:
            destination.write(_json_text(record) + "\n")
    os.replace(temporary, target)


def _append_jsonl(path: str, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as destination:
        destination.write(_json_text(record) + "\n")


def _key(record: dict) -> tuple[str, str]:
    return normalize_scenario_name(record.get("scenario", "")), str(record.get("task_id", ""))


def _task_index(task_key: str) -> int:
    match = re.fullmatch(r"task_([1-9]\d*)", task_key)
    if not match:
        raise ValueError(f"env 中的 task_id 格式非法：{task_key!r}")
    return int(match.group(1)) - 1


def _select_env_tasks(config: Config) -> list[dict]:
    """只从 env 清单选取环境目录和真实轨迹文件均已落地的任务。"""
    selected, seen = [], set()
    skipped = 0
    allowed = {normalize_scenario_name(name) for name in config.allowed_scenarios or []}
    for env in _load_jsonl(config.input_env):
        if allowed and _key(env)[0] not in allowed:
            continue
        raw_env_dir = env.get("env_dir")
        if not raw_env_dir or not _project_path(raw_env_dir).is_dir():
            skipped += 1
            continue
        scenario, task_key = env.get("scenario", ""), str(env.get("task_id", ""))
        raw_traj_dir = env.get("traj_dir")
        trajectory_dir = (_project_path(raw_traj_dir) if raw_traj_dir else
                          Path(config.trajectory_root) / f"{safe_dir_name(scenario)}_{task_key}")
        probes = sorted(trajectory_dir.glob("*/traj.json"))
        if (trajectory_dir / "traj.json").is_file():
            probes.insert(0, trajectory_dir / "traj.json")
        probes = [path for path in probes if path.is_file()]
        if not probes:
            skipped += 1
            continue
        key = _key(env)
        if not key[0] or key in seen:
            raise ValueError(f"env 清单存在空场景名或重复任务：{scenario}/{task_key}")
        seen.add(key)
        selected.append({
            "env": env, "key": key, "task_idx": _task_index(task_key),
            "env_dir": _project_path(raw_env_dir), "trajectory_dir": trajectory_dir,
            "probes": probes,
        })
    selected.sort(key=lambda item: (item["key"][0], item["task_idx"]))
    if config.limit is not None:
        selected = selected[:config.limit]
    logger.info(f"env 中已有环境与轨迹的待评判任务：{len(selected)}；未落地的记录跳过：{skipped}")
    return selected


def _has_changes(diff: dict) -> bool:
    """空 diff 传 None；行变化和 schema 变化都必须计入。"""
    return bool(diff.get("tables") or any(diff.get("summary", {}).values())
                or any(diff.get("schema_changes", {}).values()))


def _trajectory_preflight(trajectory: dict, task: str, diff: dict) -> list[str]:
    """在调用 judge 前淘汰明显无效的 rollout，避免把硬错误当作训练证据。"""
    reasons: list[str] = []
    messages = trajectory.get("messages", [])
    if trajectory.get("has_5xx") is True:
        reasons.append("轨迹出现环境 5xx")
    assistant_text = "\n".join(
        str(message.get("content", ""))
        for message in messages
        if isinstance(message, dict) and message.get("role") == "assistant"
    )
    if "<final_answer" not in assistant_text.lower():
        reasons.append("轨迹没有 final_answer")

    tool_errors = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = str(message.get("content", ""))
        if "Tool response:" in content and "Error:" in content:
            tool_errors.append(content.split("Tool response:", 1)[-1].strip().replace("\n", " ")[:300])
    if tool_errors:
        reasons.append("工具调用出现错误: " + " | ".join(tool_errors[:3]))

    if _MUTATING_TASK_RE.search(task or "") and not _has_changes(diff):
        reasons.append("任务包含写操作，但数据库 diff 为空")
    return reasons


def _prepare_probe(task: str, trajectory_path: Path, sample: dict) -> tuple[dict, dict]:
    """准备完整 judge 材料；只读取数据库，不重放轨迹、不启动环境。"""
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    if not isinstance(trajectory, dict) or not isinstance(trajectory.get("messages"), list):
        raise ValueError(f"轨迹没有 messages 列表：{trajectory_path}")
    # final_answer 已在 messages 中；不把顶层缓存字段再传一遍。
    history = {key: value for key, value in trajectory.items() if key != "final_answer"}
    initial_db = trajectory_path.parent / "initial.db"
    final_db = trajectory_path.parent / "final.db"
    if not final_db.is_file():
        raise FileNotFoundError(f"轨迹最终数据库缺失：{final_db}")
    if initial_db.is_file():
        initial_snapshot = snapshot_db(str(initial_db))
        initial_source = _portable_path(initial_db)
    elif sample.get("initial_snapshot") is not None and sample.get("task") == task:
        initial_snapshot = sample["initial_snapshot"]
        initial_source = "sample.initial_snapshot"
    else:
        raise FileNotFoundError(f"轨迹初始数据库缺失，且无对应任务的 sample 初始快照：{initial_db}")
    final_snapshot = snapshot_db(str(final_db))
    diff = diff_snapshots(initial_snapshot, final_snapshot)
    # 稳定操作顺序，使相同证据的续跑缓存不受 Python set 遍历顺序影响。
    for table in diff["tables"].values():
        table["operations"].sort(key=_json_text)
    payload = {
        "task": task,
        "trajectory": history,
        "database_state": {"initial_snapshot": initial_snapshot, "diff": diff if _has_changes(diff) else None},
    }
    metadata = {"initial_snapshot_source": initial_source, "diff_summary": diff["summary"]}
    return payload, metadata


def _judge_probe(item: dict, trajectory_path: Path, sample: dict, task: str,
                 config: Config, client_holder: list) -> dict:
    """评判一条 probe，保存证据；材料/调用错误与真实未完成分开。"""
    probe_name = (trajectory_path.parent.name if trajectory_path.parent != item["trajectory_dir"] else "probe")
    result = {"probe": probe_name, "trajectory_path": _portable_path(trajectory_path),
              "classification": "judge_error", "completed": None, "cached": False}
    try:
        payload, metadata = _prepare_probe(task, trajectory_path, sample)
        result.update(metadata)
        preflight_reasons = _trajectory_preflight(
            payload["trajectory"], task, payload["database_state"]["diff"] or {}
        )
        result["preflight_reasons"] = preflight_reasons
        fingerprint = hashlib.sha256(_json_text({
            "mode": _MODE, "model": config.model, "provider": os.getenv("AWM_SYN_LLM_PROVIDER", config.provider),
            "max_tokens": config.max_tokens, "system_prompt": LLM_JUDGE_SYSTEM_PROMPT, "input": payload,
        }).encode("utf-8")).hexdigest()
        evidence_dir = Path(config.evidence_dir) / item["key"][0] / item["key"][1] / probe_name
        input_path, result_path = evidence_dir / "judge_input.json", evidence_dir / "judge_result.json"
        result.update({"input_hash": fingerprint, "judge_input_path": _portable_path(input_path),
                       "judge_result_path": _portable_path(result_path)})
        if preflight_reasons:
            _write_json(input_path, payload)
            result.update({
                "classification": "agent_error",
                "completed": False,
                "error_stage": "preflight",
                "error": "; ".join(preflight_reasons),
                "judge_result": {"preflight_rejected": True, "reasons": preflight_reasons},
                "attempts": 0,
            })
            _write_json(result_path, {"mode": _MODE, **result})
            return result
        if config.resume and result_path.is_file():
            try:
                cached = json.loads(result_path.read_text(encoding="utf-8"))
                if (cached.get("mode") == _MODE and cached.get("input_hash") == fingerprint
                        and cached.get("classification") in _CLASSIFICATIONS):
                    result.update({"classification": cached["classification"],
                                   "completed": cached["classification"] == "complete",
                                   "judge_result": cached["judge_result"], "cached": True,
                                   "attempts": cached.get("attempts", 1)})
                    return result
            except (OSError, ValueError, KeyError):
                pass
        _write_json(input_path, payload)
    except Exception as exc:
        result.update({"error_stage": "evidence", "error": f"{type(exc).__name__}: {exc}"})
        return result

    for attempt in range(config.max_retry + 1):
        check_cancelled()
        try:
            # 每个任务使用独立客户端；若全部 probe 命中缓存，不创建客户端也不请求 API。
            if not client_holder:
                client_holder.append(GPTClient(provider=config.provider, timeout=config.request_timeout))
            classification, verdict = llm_judge(
                client_holder[0], config.model, task, payload["trajectory"], payload["database_state"],
                max_tokens=config.max_tokens,
            )
        except Exception as exc:
            classification, verdict = "judge_error", {"error": f"{type(exc).__name__}: {exc}"}
        if classification in _CLASSIFICATIONS:
            break
        logger.warning(f"{item['env']['scenario']}/{item['key'][1]}/{probe_name}："
                       f"judge 失败（{attempt + 1}/{config.max_retry + 1}）：{verdict.get('error', verdict)}")
    result.update({"classification": classification, "judge_result": verdict, "attempts": attempt + 1,
                   "completed": classification == "complete" if classification in _CLASSIFICATIONS else None})
    if classification not in _CLASSIFICATIONS:
        result.update({"error_stage": "judge", "error": verdict.get("error", "judge 返回无效分类")})
    try:
        _write_json(result_path, {"mode": _MODE, **result})
    except OSError as exc:
        logger.warning(f"judge 结果缓存保存失败：{result_path}：{exc}")
    return result


def _task_result(item: dict, task: str, probes: list[dict], model: str) -> dict:
    counts = Counter(probe["classification"] for probe in probes)
    if counts["complete"]:
        completed, status = True, "completed"
    elif probes and all(probe["classification"] in _CLASSIFICATIONS for probe in probes):
        completed, status = False, "not_completed"
    else:
        completed, status = None, "judge_error"
    env = item["env"]
    return {
        "scenario": env["scenario"], "scenario_key": item["key"][0],
        "task_id": item["task_idx"], "task_key": item["key"][1], "task": task,
        "env_dir": _portable_path(item["env_dir"]),
        "trajectory_dir": _portable_path(item["trajectory_dir"]),
        "db_path": _portable_path(_project_path(env["db_path"])) if env.get("db_path") else None,
        "status": status, "completed": completed, "judge_model": model,
        "probe_count": len(probes), "completed_probe_count": counts["complete"],
        "classification_counts": dict(counts), "probes": probes,
        "verification": {"mode": _MODE, "aggregation": "any_complete"},
    }


def judge_task(item: dict, sample: dict, config: Config) -> dict:
    """评判该 env 任务的全部已保存 probe，再汇总任务完成情况。"""
    task = item["env"].get("task") or sample.get("task")
    if not isinstance(task, str) or not task.strip():
        probes = [{"probe": path.parent.name, "classification": "judge_error", "completed": None,
                   "error_stage": "evidence", "error": "env 与对应 sample 都没有任务文本"}
                  for path in item["probes"]]
    else:
        client_holder = []
        probes = [_judge_probe(item, path, sample, task, config, client_holder) for path in item["probes"]]
    return _task_result(item, task, probes, config.model)


def _task_entry(result: dict) -> dict:
    return {key: result[key] for key in (
        "scenario", "task_id", "task_key", "task", "env_dir", "trajectory_dir", "db_path",
        "completed", "status", "probe_count", "completed_probe_count",
    )}


def _error_entries(result: dict) -> list[dict]:
    return [{"scenario": result["scenario"], "task_id": result["task_id"], "task_key": result["task_key"],
             "probe": probe["probe"], "stage": probe.get("error_stage", "judge"),
             "error": probe.get("error", "judge 未成功评判")}
            for probe in result["probes"] if probe["classification"] not in _CLASSIFICATIONS]


def run(config: Config) -> list[dict]:
    """只评判 env 产物；边完成边落盘，最终输出任务清单按自然顺序排列。"""
    items = _select_env_tasks(config)
    selected_keys = {item["key"] for item in items}
    samples = {}
    if items and config.input_sample and Path(config.input_sample).is_file():
        samples = {_key(record): record for record in _load_jsonl(config.input_sample)
                   if _key(record) in selected_keys}
    # 清单反映本次选择的 env 任务；成功的逐 probe 结果通过 evidence_dir 缓存续跑。
    for name in _OUTPUT_FIELDS:
        _write_jsonl(getattr(config, name), [])
    if not items:
        logger.info("没有已产出环境和轨迹的任务，已输出空清单；未调用 judge")
        return []

    logger.info(f"LLM judge：model={config.model}，任务并发={config.max_concurrency}，"
                f"轨迹总数={sum(len(item['probes']) for item in items)}")
    results = []
    with ThreadPoolExecutor(max_workers=min(config.max_concurrency, len(items))) as pool:
        futures = {pool.submit(judge_task, item, samples.get(item["key"], {}), config): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = _task_result(item, item["env"].get("task"), [
                    {"probe": "task", "classification": "judge_error", "completed": None,
                     "error_stage": "processing", "error": f"{type(exc).__name__}: {exc}"}
                ], config.model)
            results.append(result)
            _append_jsonl(config.output, result)
            for error in _error_entries(result):
                _append_jsonl(config.bad_output, error)
            if result["completed"] is not None:
                entry = _task_entry(result)
                _append_jsonl(config.ready_output, entry)
                _append_jsonl(config.solvable_output if result["completed"] else config.unsolvable_output, entry)
            logger.info(f"{result['scenario']}/{result['task_key']}：{result['status']}，"
                        f"完成轨迹 {result['completed_probe_count']}/{result['probe_count']} "
                        f"（任务进度 {len(results)}/{len(items)}）")

    results.sort(key=lambda result: (result["scenario_key"], result["task_id"]))
    entries = [_task_entry(result) for result in results if result["completed"] is not None]
    _write_jsonl(config.output, results)
    _write_jsonl(config.bad_output, [error for result in results for error in _error_entries(result)])
    _write_jsonl(config.ready_output, entries)
    _write_jsonl(config.solvable_output, [entry for entry in entries if entry["completed"]])
    _write_jsonl(config.unsolvable_output, [entry for entry in entries if not entry["completed"]])
    logger.info(f"judge 汇总：任务 {len(results)}，已完成 {sum(entry['completed'] for entry in entries)}，"
                f"未完成 {sum(not entry['completed'] for entry in entries)}，"
                f"尚不能确定 {len(results) - len(entries)}；结果：{config.output}")
    return results


def main():
    from simpleArgParser import parse_args
    config: Config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)
