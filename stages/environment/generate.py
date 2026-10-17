"""Command line entry point for task-level single-file environment generation.

续跑机制与 specific 阶段一致：默认 resume=True + append_jsonl 追加写，
已通过任务由 resume_keys 校验后跳过；显式关闭 resume 时清空成功清单。
坏任务文件每次启动无条件清空，只记录本次运行产生的失败。
产物只有 output/environment/{gen_envs_ma,env_generate_bad}.jsonl 与 {envs,trajectory}/ 目录。
"""
import time
from collections import Counter
from pathlib import Path
from loguru import logger
from lib.interrupt import run_cli, check_cancelled, as_completed, InterruptibleThreadPoolExecutor
from .config import Config
from .pipeline import generate_environment
from .storage import append_jsonl, merge_inputs, resume_keys, task_key


def run(config):
    items, input_failures = merge_inputs(config)
    root = Path(config.envs_dir).resolve().parents[2]
    done = resume_keys(config.output, root) if config.resume else set()
    skipped = sum(task_key(item) in done for item in items)
    pending = [item for item in items if task_key(item) not in done]
    if config.allowed_scenarios:
        pending = [item for item in pending if item["scenario"] in set(config.allowed_scenarios)]
    eligible = len(pending)
    if config.limit is not None:
        pending = pending[:config.limit]
    for path in (config.output, config.bad_output):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    if config.resume:
        Path(config.output).touch(exist_ok=True)
    else:
        Path(config.output).write_text("", encoding="utf-8")
    # 坏任务文件每次启动清空、只记录本次运行：没通过的任务无论是否续跑都会在本轮重新尝试并
    # 重新写入，跨运行保留旧坏记录只会让文件越滚越大、掩盖本轮真实失败情况。
    Path(config.bad_output).write_text("", encoding="utf-8")
    for failure in input_failures:
        append_jsonl(config.bad_output, failure)
    total = len(pending)
    logger.info(f"[续跑跳过={skipped} 输入淘汰={len(input_failures)} 本轮待处理={total}，"
                f"受 limit 限制留待后续={eligible-total}，并发={config.max_concurrency}]")
    success = discarded = 0
    counts = Counter(failure.get("stage", "unknown") for failure in input_failures)
    completed = 0
    started = time.monotonic()
    with InterruptibleThreadPoolExecutor(max_workers=config.max_concurrency) as pool:
        futures = {pool.submit(generate_environment, item, config): item for item in pending}
        for future in as_completed(futures):
            check_cancelled()
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.exception(f"{item['scenario']}/{item['task_id']}: 未预期异常")
                result = {**{key: item[key] for key in ("scenario", "task_id", "task")}, "stage": "exception", "reason": f"{type(exc).__name__}: {exc}"}
            completed += 1
            if result.get("stage"):
                append_jsonl(config.bad_output, result)
                discarded += 1
                counts[result["stage"]] += 1
                logger.warning(f"[{completed}/{total} 淘汰] {item['scenario']}/{item['task_id']} {result['stage']}: {result.get('reason')}")
            else:
                append_jsonl(config.output, result)
                success += 1
                logger.success(f"[{completed}/{total} 通过] {item['scenario']}/{item['task_id']} fixes={result.get('fix_count', 0)}")
            logger.info(f"[本轮进度 {completed}/{total}，{completed / total:.1%}] 通过={success}，淘汰={discarded}，"
                        f"未完成={total - completed}，累计耗时={time.monotonic() - started:.1f}s")
    summary = ", ".join(f"{stage}={count}" for stage, count in sorted(counts.items())) or "无"
    logger.info(f"[完成] 本轮通过={success}/{total} 本轮淘汰={discarded} 明细={summary} 耗时={time.monotonic()-started:.1f}s 输出={config.output}")
    return {"success": success, "discarded": discarded, "input_errors": len(input_failures)}


def main():
    from simpleArgParser import parse_args
    config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)
