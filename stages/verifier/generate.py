"""评判 environment 阶段保存的单条 rollout。

每个 environment 任务只保存一份最终环境、一条 traj.json、initial.db 和
final.db。本阶段只构造数据库证据并调用一次 LLM judge，不重放环境、不生成
任务专属验证代码，也不使用关键词规则预判任务是否完成。

在 ma_upgrade 目录运行：
  python -m stages.verifier.generate --model <judge-model>
  python -m stages.verifier.generate --model <judge-model> --max_concurrency 5

默认 resume=True。每个任务的 judge 输入和结果都会原子写入 evidence 目录；
主清单在每个任务结束后也会原子更新，中断后可以继续运行。
"""

import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from loguru import logger

from lib.db_diff import diff_snapshots, snapshot_db
from lib.gpt import GPTClient
from lib.interrupt import (
    InterruptibleThreadPoolExecutor as ThreadPoolExecutor,
    as_completed,
    check_cancelled,
    run_cli,
)
from lib.llm_judge import LLM_JUDGE_SYSTEM_PROMPT, llm_judge
from lib.tools import normalize_scenario_name, safe_dir_name

_OUTPUT_DIR = _PROJECT_ROOT / "output" / "verifier"
_MODE = "env_rollout_llm_judge_single_trajectory_v2"
_JUDGE_CLASSES = {"complete", "incomplete", "agent_error", "server_error"}
_ALL_CLASSES = _JUDGE_CLASSES | {"evidence_error", "judge_error"}
_OUTPUT_NAMES = ("output", "bad_output", "ready_output", "solvable_output", "unsolvable_output")


class EvidenceError(ValueError):
    """环境、轨迹或数据库材料无法支持可靠评判。"""


@dataclass
class Config:
    """Verifier 配置。相对路径均以 ma_upgrade 为根目录。"""

    input_env: str = str(_PROJECT_ROOT / "output" / "environment" / "gen_envs_ma.jsonl")
    input_sample: str = str(_PROJECT_ROOT / "output" / "sample" / "gen_sample_ma.jsonl")
    trajectory_root: str = str(_PROJECT_ROOT / "output" / "environment" / "trajectory")
    output: str = str(_OUTPUT_DIR / "gen_verifier_ma.jsonl")
    bad_output: str = str(_OUTPUT_DIR / "verifier_bad_tasks.jsonl")
    ready_output: str = str(_OUTPUT_DIR / "rl_ready_tasks.jsonl")
    solvable_output: str = str(_OUTPUT_DIR / "solvable_tasks.jsonl")
    unsolvable_output: str = str(_OUTPUT_DIR / "unsolvable_tasks.jsonl")
    evidence_dir: str = str(_OUTPUT_DIR / "judge_evidence")
    model: str = "your-llm-model-name"
    provider: str = "azure"
    max_concurrency: int = 5
    max_retry: int = 2
    request_timeout: float = 600.0
    max_tokens: int = 4096
    resume: bool = True
    allowed_scenarios: list[str] | None = None
    limit: int | None = None

    def pre_process(self) -> None:
        for name in ("input_env", "input_sample", "trajectory_root", "evidence_dir", *_OUTPUT_NAMES):
            value = getattr(self, name)
            if value:
                setattr(self, name, str(_project_path(value)))
        if not Path(self.input_env).is_file():
            raise FileNotFoundError(f"environment 清单不存在：{self.input_env}")
        outputs = [Path(getattr(self, name)) for name in _OUTPUT_NAMES]
        if any(path.suffix.lower() != ".jsonl" for path in outputs):
            raise ValueError("所有清单输出都必须使用 .jsonl 后缀")
        if len(set(outputs)) != len(outputs):
            raise ValueError("各输出清单必须使用不同路径")
        inputs = {Path(self.input_env).resolve(), Path(self.input_sample).resolve()}
        if any(path.resolve() in inputs for path in outputs):
            raise ValueError("输出清单不能覆盖 environment 或 sample 输入")
        if self.model == "your-llm-model-name":
            self.model = os.environ.get("AWM_SYN_OVERRIDE_MODEL", self.model)
        if not self.model or self.model == "your-llm-model-name":
            raise ValueError("请通过 --model 或 AWM_SYN_OVERRIDE_MODEL 设置 judge 模型")
        if self.max_concurrency < 1 or self.max_retry < 0 or self.max_tokens < 1 or self.request_timeout <= 0:
            raise ValueError("并发、超时和 max_tokens 必须为正数，max_retry 不能为负数")
        if self.limit is not None and self.limit < 0:
            raise ValueError("limit 不能为负数")


def _project_path(raw: str | Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0] == _PROJECT_ROOT.name:
        path = Path(*path.parts[1:])
    return (_PROJECT_ROOT / path).resolve()


def _portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_json_text(value), encoding="utf-8")
    os.replace(temporary, path)


def _write_jsonl(path: str | Path, records: list[dict]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(_json_text(record) + "\n")
    os.replace(temporary, target)


def _load_jsonl(path: str | Path) -> list[dict]:
    records = []
    with Path(path).open(encoding="utf-8") as source:
        for line_no, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} 不是有效 JSON：{exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_no} 不是 JSON 对象")
            records.append(record)
    return records


def _key(record: dict) -> tuple[str, str]:
    raw_task_id = record.get("task_key", record.get("task_id", ""))
    if isinstance(raw_task_id, int):
        task_key = f"task_{raw_task_id + 1}"
    else:
        task_key = str(raw_task_id)
    return normalize_scenario_name(str(record.get("scenario", ""))), task_key


def _task_index(task_key: str) -> int:
    prefix, _, number = task_key.partition("_")
    if prefix != "task" or not number.isdigit() or int(number) < 1:
        raise EvidenceError(f"task_id 格式应为 task_N：{task_key!r}")
    return int(number) - 1


def _has_changes(diff: dict) -> bool:
    summary = diff.get("summary", {})
    schema = diff.get("schema_changes", {})
    return bool(diff.get("tables") or any(summary.values()) or any(schema.values()))


def _sort_diff(diff: dict) -> dict:
    for table in diff.get("tables", {}).values():
        table.get("operations", []).sort(key=_json_text)
    return diff


def _find_trajectory(env: dict, scenario: str, task_key: str, root: Path) -> tuple[Path, str | None]:
    raw_dir = env.get("traj_dir")
    directory = _project_path(raw_dir) if raw_dir else root / f"{safe_dir_name(scenario)}_{task_key}"
    path = directory / "traj.json"
    return path, None if path.is_file() else f"单轨迹文件不存在：{_portable_path(path)}"


def _select_tasks(config: Config) -> list[dict]:
    """读取 environment 清单；每条记录最多对应一个 traj.json。"""
    allowed = {normalize_scenario_name(name) for name in config.allowed_scenarios or []}
    selected, seen = [], {}
    for env in _load_jsonl(config.input_env):
        key = _key(env)
        if allowed and key[0] not in allowed:
            continue
        if not key[0]:
            raise ValueError("environment 清单存在空场景名")
        if key in seen:
            selected[seen[key]]["input_errors"].append(
                f"environment 清单重复包含任务：{key[0]}/{key[1]}"
            )
            continue
        errors = []
        try:
            task_idx = _task_index(key[1])
        except EvidenceError as exc:
            task_idx, errors = None, [str(exc)]
        env_dir = _project_path(env.get("env_dir", "")) if env.get("env_dir") else None
        trajectory_path, trajectory_error = _find_trajectory(
            env, str(env.get("scenario", "")), key[1], Path(config.trajectory_root)
        )
        if trajectory_error:
            errors.append(trajectory_error)
        if env_dir is None or not env_dir.is_dir():
            errors.append(f"环境目录不存在：{env.get('env_dir')!r}")
        selected.append({
            "env": env,
            "key": key,
            "task_idx": task_idx,
            "env_dir": env_dir,
            "trajectory_dir": trajectory_path.parent,
            "trajectory_path": trajectory_path,
            "input_errors": errors,
        })
        seen[key] = len(selected) - 1
    selected.sort(key=lambda item: (
        item["key"][0], item["task_idx"] if item["task_idx"] is not None else 10**12
    ))
    if config.limit is not None:
        selected = selected[:config.limit]
    invalid = sum(bool(item["input_errors"]) for item in selected)
    logger.info(f"environment 待评判任务：{len(selected)}，材料预检查失败：{invalid}，并发：{config.max_concurrency}")
    return selected


def _prepare_evidence(item: dict, sample: dict) -> tuple[dict, dict]:
    """读取唯一轨迹和数据库，构造给 judge 的完整证据。"""
    if item["input_errors"]:
        raise EvidenceError("；".join(item["input_errors"]))
    env = item["env"]
    task = env.get("task")
    if not isinstance(task, str) or not task.strip():
        task = sample.get("task")
    if not isinstance(task, str) or not task.strip():
        raise EvidenceError("environment 和 sample 都没有有效任务文本")
    if sample.get("task") and env.get("task") and sample["task"] != env["task"]:
        raise EvidenceError("environment 与 sample 的任务文本不一致")

    trajectory_path: Path = item["trajectory_path"]
    try:
        trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"轨迹读取失败：{type(exc).__name__}: {exc}") from exc
    if not isinstance(trajectory, dict) or not isinstance(trajectory.get("messages"), list):
        raise EvidenceError("traj.json 必须是包含 messages 列表的 JSON 对象")

    initial_db = trajectory_path.parent / "initial.db"
    final_db = trajectory_path.parent / "final.db"
    if not final_db.is_file():
        raise EvidenceError(f"最终数据库不存在：{_portable_path(final_db)}")
    if initial_db.is_file():
        initial_snapshot = snapshot_db(str(initial_db))
        initial_source = _portable_path(initial_db)
    elif sample.get("initial_snapshot") is not None:
        initial_snapshot = sample["initial_snapshot"]
        initial_source = "sample.initial_snapshot"
    else:
        raise EvidenceError(f"初始数据库不存在：{_portable_path(initial_db)}")
    try:
        final_snapshot = snapshot_db(str(final_db))
    except Exception as exc:
        raise EvidenceError(f"最终数据库读取失败：{type(exc).__name__}: {exc}") from exc
    diff = _sort_diff(diff_snapshots(initial_snapshot, final_snapshot))
    history = {key: value for key, value in trajectory.items() if key != "final_answer"}
    payload = {
        "task": task,
        "trajectory": history,
        "database_state": {
            "initial_snapshot": initial_snapshot,
            "diff": diff if _has_changes(diff) else None,
        },
    }
    return payload, {
        "trajectory_path": _portable_path(trajectory_path),
        "initial_snapshot_source": initial_source,
        "diff_summary": diff["summary"],
    }


def _evidence_paths(config: Config, item: dict) -> tuple[Path, Path]:
    directory = Path(config.evidence_dir) / item["key"][0] / item["key"][1]
    return directory / "judge_input.json", directory / "judge_result.json"


def _fingerprint(config: Config, payload: dict) -> str:
    value = {
        "mode": _MODE,
        "model": config.model,
        "provider": os.getenv("AWM_SYN_LLM_PROVIDER", config.provider),
        "max_tokens": config.max_tokens,
        "system_prompt": LLM_JUDGE_SYSTEM_PROMPT,
        "input": payload,
    }
    return hashlib.sha256(_json_text(value).encode("utf-8")).hexdigest()


def _base_result(item: dict, task: str | None, config: Config) -> dict:
    env = item["env"]
    return {
        "scenario": env.get("scenario", ""),
        "scenario_key": item["key"][0],
        "task_id": item["task_idx"],
        "task_key": item["key"][1],
        "task": task,
        "env_dir": _portable_path(item["env_dir"]) if item["env_dir"] else None,
        "trajectory_dir": _portable_path(item["trajectory_dir"]),
        "trajectory_path": _portable_path(item["trajectory_path"]),
        "db_path": _portable_path(_project_path(env["db_path"])) if env.get("db_path") else None,
        "judge_model": config.model,
        "verification": {"mode": _MODE, "aggregation": "single_trajectory"},
    }


def _judge_task(item: dict, sample: dict, config: Config) -> dict:
    env_task = item["env"].get("task")
    task = env_task if isinstance(env_task, str) and env_task.strip() else sample.get("task")
    result = _base_result(item, task, config)
    input_path, result_path = _evidence_paths(config, item)
    result.update({
        "classification": "evidence_error", "status": "bad", "completed": None,
        "cached": False, "attempts": 0, "judge_input_path": _portable_path(input_path),
        "judge_result_path": _portable_path(result_path),
    })
    try:
        payload, metadata = _prepare_evidence(item, sample)
    except EvidenceError as exc:
        result.update({"error_stage": "evidence", "error": str(exc)})
        _write_json(result_path, {"mode": _MODE, **result})
        return result
    except Exception as exc:
        result.update({"error_stage": "evidence", "error": f"{type(exc).__name__}: {exc}"})
        _write_json(result_path, {"mode": _MODE, **result})
        return result

    result.update(metadata)
    fingerprint = _fingerprint(config, payload)
    result["input_hash"] = fingerprint
    if config.resume and result_path.is_file():
        try:
            cached = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
        if (
            isinstance(cached, dict)
            and cached.get("mode") == _MODE
            and cached.get("input_hash") == fingerprint
            and cached.get("classification") in _JUDGE_CLASSES
        ):
            cached = dict(cached)
            cached["cached"] = True
            return cached

    _write_json(input_path, payload)
    client = GPTClient(provider=config.provider, timeout=config.request_timeout)
    classification, verdict, attempts = "judge_error", {"error": "judge 未执行"}, 0
    for attempts in range(1, config.max_retry + 2):
        check_cancelled()
        classification, verdict = llm_judge(
            client, config.model, task, payload["trajectory"], payload["database_state"],
            max_tokens=config.max_tokens,
        )
        if classification in _JUDGE_CLASSES:
            break
        logger.warning(
            f"{item['key'][0]}/{item['key'][1]}：judge 请求失败，"
            f"重试 {attempts}/{config.max_retry + 1}：{verdict}"
        )

    classification = classification if classification in _ALL_CLASSES else "judge_error"
    status = (
        "completed" if classification == "complete"
        else "not_completed" if classification in {"incomplete", "agent_error"}
        else "bad"
    )
    result.update({
        "classification": classification,
        "judge_result": verdict,
        "attempts": attempts,
        "completed": True if classification == "complete" else False if status == "not_completed" else None,
        "status": status,
    })
    if status == "bad":
        result["error_stage"] = "judge" if classification == "judge_error" else "environment"
        result["error"] = (
            verdict.get("error", "judge 判定为服务端错误")
            if isinstance(verdict, dict) else str(verdict)
        )
    _write_json(result_path, {"mode": _MODE, **result})
    return result


def _entry(result: dict) -> dict:
    return {
        key: result.get(key)
        for key in (
            "scenario", "scenario_key", "task_id", "task_key", "task", "env_dir",
            "trajectory_dir", "trajectory_path", "db_path", "classification", "status", "completed",
        )
    }


def _bad_entry(result: dict) -> dict:
    return {
        "scenario": result.get("scenario"),
        "task_id": result.get("task_id"),
        "task_key": result.get("task_key"),
        "stage": result.get("error_stage", "evidence"),
        "classification": result.get("classification"),
        "error": result.get("error", "无法可靠评判"),
        "trajectory_path": result.get("trajectory_path"),
    }


def _sort_results(results: dict[tuple[str, str], dict]) -> list[dict]:
    return sorted(
        results.values(),
        key=lambda row: (
            row.get("scenario_key", ""),
            row.get("task_id") if isinstance(row.get("task_id"), int) else 10**12,
        ),
    )


def _flush_outputs(config: Config, results: dict[tuple[str, str], dict]) -> None:
    ordered = _sort_results(results)
    bad = [_bad_entry(row) for row in ordered if row.get("status") == "bad"]
    ready = [_entry(row) for row in ordered if row.get("completed") is not None]
    solvable = [row for row in ready if row.get("completed") is True]
    unsolvable = [row for row in ready if row.get("completed") is False]
    _write_jsonl(config.output, ordered)
    _write_jsonl(config.bad_output, bad)
    _write_jsonl(config.ready_output, ready)
    _write_jsonl(config.solvable_output, solvable)
    _write_jsonl(config.unsolvable_output, unsolvable)


def run(config: Config) -> list[dict]:
    """执行任务级评判，边完成边原子写入五份清单。"""
    selected = _select_tasks(config)
    selected_keys = {item["key"] for item in selected}
    samples = {}
    if config.input_sample and Path(config.input_sample).is_file():
        samples = {
            _key(row): row
            for row in _load_jsonl(config.input_sample)
            if _key(row) in selected_keys
        }

    # 每次重新构造证据并核对任务指纹，避免 environment 产物更新后沿用旧判定。
    # 指纹匹配时 _judge_task 会复用缓存，不会再次请求 LLM。
    results: dict[tuple[str, str], dict] = {}
    if config.resume and Path(config.evidence_dir).exists():
        logger.info("断点续传已启用：将校验每个任务的证据指纹，缓存命中时跳过模型请求")
    _flush_outputs(config, results)
    pending = selected
    if not pending:
        counts = Counter(row.get("status") for row in results.values())
        logger.info(f"无需请求 judge：完成={counts['completed']}，未完成={counts['not_completed']}，坏任务={counts['bad']}")
        return _sort_results(results)

    counters = Counter(row.get("status") for row in results.values())
    completed_count = len(results)
    logger.info(f"开始 verifier：待处理 {len(pending)} 个任务，模型={config.model}")
    try:
        with ThreadPoolExecutor(max_workers=min(config.max_concurrency, len(pending))) as pool:
            futures = {
                pool.submit(_judge_task, item, samples.get(item["key"], {}), config): item
                for item in pending
            }
            for future in as_completed(futures):
                check_cancelled()
                item = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = _base_result(item, item["env"].get("task"), config)
                    result.update({
                        "classification": "judge_error", "status": "bad", "completed": None,
                        "cached": False, "attempts": 0, "error_stage": "processing",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                results[item["key"]] = result
                counters[result.get("status", "bad")] += 1
                completed_count += 1
                _flush_outputs(config, results)
                logger.info(
                    f"[进度 {completed_count}/{len(selected)} {completed_count / len(selected):.1%}] "
                    f"{item['key'][0]}/{item['key'][1]} -> {result.get('classification')}；"
                    f"完成={counters['completed']}，未完成={counters['not_completed']}，坏任务={counters['bad']}"
                )
    finally:
        _flush_outputs(config, results)

    ordered = _sort_results(results)
    counts = Counter(row.get("status") for row in ordered)
    logger.success(
        f"verifier 完成：总任务={len(ordered)}，完成={counts['completed']}，"
        f"未完成={counts['not_completed']}，坏任务={counts['bad']}；"
        f"solvable={sum(row.get('completed') is True for row in ordered)}，"
        f"unsolvable={sum(row.get('completed') is False for row in ordered)}；输出={config.output}"
    )
    return ordered


def main() -> None:
    from simpleArgParser import parse_args
    config: Config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)
