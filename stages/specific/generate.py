"""新版任务级 API 规范阶段入口。

按 sample 保留任务关联 task/db，运行 Planner → Designer → 规则校验 → Reviewer；
失败交给 Fixer，修复后重新完整检查。成功产物只保存业务 API，不生成文件清单。
在独立项目根目录 ma_upgrade 下运行：python -m stages.specific.generate。
"""

import sys
import time
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from loguru import logger
from lib.interrupt import run_cli, check_cancelled, as_completed
from lib.interrupt import InterruptibleThreadPoolExecutor as ThreadPoolExecutor
from stages.specific.config import Config
from stages.specific.pipeline import generate_spec_for_task
from stages.specific.storage import append_jsonl, load_items, resume_keys, task_key


def run(config):
    items, failures = load_items(config)
    done = resume_keys(config.output) if config.resume else set()
    skipped = sum(task_key(item) in done for item in items)
    items = [item for item in items if task_key(item) not in done]
    eligible = len(items)
    if config.limit is not None:
        items = items[:config.limit]
    for path in (config.output, config.bad_output):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not config.resume:
        Path(config.output).write_text("", encoding="utf-8")
    else:
        Path(config.output).touch(exist_ok=True)
    # 坏任务文件每次启动清空、只记录本次运行：没通过的任务无论是否续跑都会在本轮重新尝试并
    # 重新写入，跨运行保留旧坏记录只会让文件越滚越大、掩盖本轮真实失败情况。
    Path(config.bad_output).write_text("", encoding="utf-8")
    for failure in failures:
        append_jsonl(config.bad_output, failure)
    logger.info(
        f"[进度] 续跑跳过={skipped}，输入淘汰={len(failures)}，"
        f"本轮待处理={len(items)}，受 limit 限制留待后续={eligible - len(items)}，"
        f"并发={config.max_concurrency}"
    )
    success = 0
    discarded = 0
    stage_counts = Counter(failure.get("stage", "unknown") for failure in failures)
    completed = 0
    total = len(items)
    started = time.monotonic()
    logger.info(f"[本轮进度 0/{total}] 通过=0，淘汰=0，未完成={total}")
    for failure in failures:
        logger.warning(
            f"[淘汰][输入] {failure['scenario']}/{failure['task_id']}："
            f"{failure.get('reason', '未知原因')}"
        )
    with ThreadPoolExecutor(max_workers=config.max_concurrency) as pool:
        futures = {pool.submit(generate_spec_for_task, item, config): item for item in items}
        for future in as_completed(futures):
            check_cancelled()
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.exception(f"{item['scenario']}/{item['task_id']}: 未预期异常")
                result = {k: item[k] for k in ("scenario", "task_id", "task")}
                result.update(stage="exception", reason=f"{type(exc).__name__}: {exc}")
            if "reason" in result:
                append_jsonl(config.bad_output, result)
                discarded += 1
                stage = result.get("stage", "unknown")
                stage_counts[stage] += 1
                status = "异常" if stage == "exception" else "淘汰"
                logger.warning(
                    f"[本轮进度 {completed + 1}/{total}][{status}] "
                    f"{item['scenario']}/{item['task_id']} stage={stage}："
                    f"{result.get('reason', '未知原因')}"
                )
            else:
                append_jsonl(config.output, result)
                success += 1
                logger.success(
                    f"[本轮进度 {completed + 1}/{total}][通过] "
                    f"{item['scenario']}/{item['task_id']}"
                )
            completed += 1
            logger.info(
                f"[本轮进度 {completed}/{total}，{completed / total:.1%}] "
                f"通过={success}，淘汰={discarded}，未完成={total - completed}，"
                f"累计耗时={time.monotonic() - started:.1f}s"
            )
    summary = ", ".join(f"{stage}={count}" for stage, count in sorted(stage_counts.items())) or "无"
    logger.info(
        f"[完成] 本轮通过={success}/{len(items)}，模型/流程淘汰={discarded}，"
        f"输入淘汰={len(failures)}，淘汰明细={summary}，输出={config.output}"
    )
    return {"success": success, "attempted": len(items), "input_errors": len(failures),
            "discarded": discarded, "discarded_by_stage": dict(stage_counts)}


def main():
    from simpleArgParser import parse_args
    config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)
