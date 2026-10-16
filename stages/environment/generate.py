"""Command line entry point for task-level single-file environment generation."""
import shutil
import time
from collections import Counter
from pathlib import Path
from loguru import logger
from lib.interrupt import run_cli, check_cancelled, as_completed, InterruptibleThreadPoolExecutor
from .config import Config
from .pipeline import generate_environment
from .storage import merge_inputs, read_jsonl, task_key, write_jsonl


def run(config):
    items, input_failures = merge_inputs(config)
    previous = read_jsonl(config.output) if config.resume and Path(config.output).is_file() else []
    completed = {task_key(row): row for row in previous if row.get("env_dir") and row.get("traj_dir")}
    pending = [item for item in items if task_key(item) not in completed]
    if config.allowed_scenarios:
        pending = [item for item in pending if item["scenario"] in set(config.allowed_scenarios)]
    if config.limit is not None:
        pending = pending[:config.limit]
    Path(config.output).parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(config.output, completed.values())
    write_jsonl(config.bad_output, input_failures)
    logger.info(f"[进度 0/{len(pending)}] 输入={len(items)} 续跑={len(completed)} 输入淘汰={len(input_failures)} 并发={config.max_concurrency}")
    failures = list(input_failures)
    counts = Counter()
    success = 0
    with InterruptibleThreadPoolExecutor(max_workers=config.max_concurrency) as pool:
        futures = {pool.submit(generate_environment, item, config): item for item in pending}
        for future in as_completed(futures):
            check_cancelled()
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"scenario": item["scenario"], "task_id": item["task_id"], "task": item["task"], "stage": "exception", "reason": f"{type(exc).__name__}: {exc}"}
            if result.get("stage"):
                failures.append(result)
                counts[result["stage"]] += 1
                logger.warning(f"[淘汰] {item['scenario']}/{item['task_id']} {result['stage']}: {result.get('reason')}")
            else:
                completed[task_key(result)] = result
                success += 1
                logger.success(f"[通过] {item['scenario']}/{item['task_id']} fixes={result.get('fix_count', 0)}")
            write_jsonl(config.output, completed.values())
            write_jsonl(config.bad_output, failures)
            done = success + sum(1 for row in failures if task_key(row) in {task_key(x) for x in pending})
            logger.info(f"[进度 {done}/{len(pending)}] 通过={success} 淘汰={done-success} 剩余={len(pending)-done}")
    logger.info(f"[完成] 通过={success} 淘汰={len(pending)-success} 原因={dict(counts)} 输出={config.output}")
    return {"success": success, "failed": len(pending)-success}


def main():
    from simpleArgParser import parse_args
    config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)
