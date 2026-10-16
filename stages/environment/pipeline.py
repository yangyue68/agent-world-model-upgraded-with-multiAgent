"""One task: generate -> deterministic checks -> review -> rollout -> repair."""
import json
import shutil
import uuid
from pathlib import Path
from loguru import logger
from lib.db_diff import snapshot_db, diff_snapshots
from lib.interrupt import check_cancelled
from lib.single_file_env import ENV_VERSION
from lib.tools import safe_dir_name
from .artifacts import cleanup, publish, save_json
from .llm import build_agents, extract_code, parse_review, request, ReplyError
from .runner import run_probe, validate_candidate
from .storage import portable
from .validation import compact, generated_code_runtime_errors, normalize_code, repair_name_error_import, scan_trajectory, summarize_problems, trajectory_evidence


def _payload(item):
    return json.dumps({"scenario": item["scenario"], "task_id": item["task_id"], "task": item["task"],
                       "api_spec": item.get("api_spec", {}), "db_schema": item.get("db_schema", {}),
                       "initial_snapshot": item.get("initial_snapshot", {})}, ensure_ascii=False, default=str)


def generate_environment(item, config):
    label = f"{item['scenario']}/{item['task_id']}"
    stamp = f"{safe_dir_name(item['scenario'])[:60]}_{item['task_id']}_{uuid.uuid4().hex[:10]}"
    work = Path(config.envs_dir) / ".work" / stamp
    work.mkdir(parents=True, exist_ok=True)
    agents = build_agents(config, label, work / "_raw_llm")
    base = {key: item[key] for key in ("scenario", "task_id", "task")}
    context = _payload(item)
    try:
        code = normalize_code(request(agents["generator"], context, extract_code, config.request_retry), item.get("api_spec"))
        for repair_no in range(config.max_fix_retry + 1):
            check_cancelled()
            round_dir = work / f"round_{repair_no}"
            round_dir.mkdir(parents=True, exist_ok=True)
            (round_dir / "main.py").write_text(code, encoding="utf-8")
            save_json(round_dir / "environment.json", {"env_version": ENV_VERSION, "code_fingerprint": __import__("hashlib").sha256(code.encode()).hexdigest()})
            problems = validate_candidate(code, round_dir, item, config)
            if not problems:
                review_input = json.dumps({"input": json.loads(context), "current_main_py": code}, ensure_ascii=False, default=str)
                review = request(agents["reviewer"], review_input, parse_review, config.request_retry)
                save_json(round_dir / "review.json", review)
                if review["verdict"] == "upstream_conflict":
                    return {**base, "stage": "upstream_conflict", "reason": "; ".join(review["issues"]), "diagnostics_dir": portable(work, Path(config.envs_dir).parents[2])}
                if review["verdict"] == "environment_bug":
                    problems = review["issues"]
            accepted = []
            if not problems:
                for probe_no in range(config.rollout_count):
                    for attempt in range(config.max_probe_retry + 1):
                        check_cancelled()
                        run_dir = round_dir / "probes" / f"run_{probe_no}_{attempt}"
                        run_dir.mkdir(parents=True, exist_ok=True)
                        trajectory = run_probe(round_dir, run_dir, item, config)
                        trajectory["http_events"] = trajectory.get("http_events", [])
                        initial = snapshot_db(str(run_dir / "initial.db"))
                        final = snapshot_db(str(run_dir / "final.db"))
                        trajectory["database_diff"] = diff_snapshots(initial, final) or None
                        trajectory["error_scan"] = scan_trajectory(trajectory)
                        save_json(run_dir / "traj.json", trajectory)
                        calls = [event for event in trajectory.get("http_events", []) if event.get("status", 0) < 500]
                        if trajectory.get("has_5xx") or trajectory["error_scan"]["environment_errors"]:
                            generated_errors = generated_code_runtime_errors(trajectory["error_scan"]["environment_errors"])
                            if generated_errors:
                                problems = generated_errors
                                break
                            error_review_input = json.dumps({
                                "input": json.loads(context),
                                "current_main_py": code,
                                "trajectory": trajectory_evidence(trajectory),
                                "initial_snapshot": compact(initial),
                                "diff": compact(trajectory["database_diff"]),
                            }, ensure_ascii=False, default=str)
                            error_review = request(
                                agents["trajectory_reviewer"],
                                error_review_input,
                                parse_review,
                                config.request_retry,
                            )
                            trajectory["environment_review"] = error_review
                            save_json(run_dir / "traj.json", trajectory)
                            if error_review["verdict"] == "upstream_conflict":
                                return {**base, "stage": "upstream_conflict", "reason": "; ".join(error_review["issues"]), "diagnostics_dir": portable(work, Path(config.envs_dir).parents[2])}
                            if error_review["verdict"] == "environment_bug":
                                problems = error_review["issues"] or trajectory["error_scan"]["environment_errors"]
                                break
                            accepted.append(run_dir)
                            break
                        if not calls or trajectory.get("total_calls", 0) == 0:
                            if attempt < config.max_probe_retry:
                                continue
                            return {**base, "stage": "probe_incomplete", "reason": "rollout 没有产生有效业务工具调用", "diagnostics_dir": portable(work, Path(config.envs_dir).parents[2])}
                        review_input = json.dumps({"input": json.loads(context), "current_main_py": code,
                                                   "trajectory": trajectory_evidence(trajectory),
                                                   "initial_snapshot": compact(initial), "diff": compact(trajectory["database_diff"])}, ensure_ascii=False, default=str)
                        tr_review = request(agents["trajectory_reviewer"], review_input, parse_review, config.request_retry)
                        trajectory["environment_review"] = tr_review
                        save_json(run_dir / "traj.json", trajectory)
                        if tr_review["verdict"] == "upstream_conflict":
                            return {**base, "stage": "upstream_conflict", "reason": "; ".join(tr_review["issues"]), "diagnostics_dir": portable(work, Path(config.envs_dir).parents[2])}
                        if tr_review["verdict"] == "environment_bug":
                            problems = tr_review["issues"]
                            break
                        accepted.append(run_dir)
                        break
                    if problems:
                        break
            if not problems:
                env_dir, traj_dir = publish(round_dir, accepted, config, stamp)
                cleanup(work)
                return {**base, "env_version": ENV_VERSION, "input_fingerprint": item["input_fingerprint"],
                        "code_fingerprint": __import__("hashlib").sha256(code.encode()).hexdigest(),
                        "db_path": portable(item["db_path"], Path(config.envs_dir).parents[2]),
                        "env_dir": env_dir, "traj_dir": traj_dir, "fix_count": repair_no, "probe_count": len(accepted)}
            save_json(round_dir / "problems.json", problems)
            repaired_code = repair_name_error_import(code, problems)
            if repaired_code != code:
                code = normalize_code(repaired_code, item.get("api_spec"))
                continue
            if repair_no >= config.max_fix_retry:
                return {**base, "stage": "environment_bug", "reason": "; ".join(map(str, problems)), "diagnostics_dir": portable(work, Path(config.envs_dir).parents[2])}
            fixer_input = json.dumps({"input": json.loads(context), "current_main_py": code,
                                      "problems": summarize_problems(problems),
                                      "instruction": "Fix each distinct root cause shown by the final exception or validation message. Return a complete corrected main.py."}, ensure_ascii=False, default=str)
            code = normalize_code(request(agents["fixer"], fixer_input, extract_code, config.request_retry), item.get("api_spec"))
    except ReplyError as exc:
        return {**base, "stage": "model_request", "reason": str(exc), "diagnostics_dir": portable(work, Path(config.envs_dir).parents[2])}
    except Exception as exc:
        logger.exception(f"[{label}] environment pipeline failed")
        return {**base, "stage": "runtime", "reason": f"{type(exc).__name__}: {exc}", "diagnostics_dir": portable(work, Path(config.envs_dir).parents[2])}
