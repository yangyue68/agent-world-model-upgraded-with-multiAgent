# -*- coding: utf-8 -*-
"""checkpoint 和坏任务清单。"""

import json
import os
import time

from loguru import logger


def save_checkpoint(model, tokenizer, output_dir: str, step: int):
    checkpoint_dir = os.path.join(output_dir, "checkpoint")
    os.makedirs(checkpoint_dir, exist_ok=True)
    model.save_pretrained(checkpoint_dir)
    tokenizer.save_pretrained(checkpoint_dir)
    with open(os.path.join(checkpoint_dir, "progress.json"), "w", encoding="utf-8") as f:
        json.dump({"step": step}, f)
    logger.info(f"已保存 checkpoint（已训练 {step} 个任务）到 {checkpoint_dir}")


def build_bad_tasks_path(config) -> str:
    source_name = ""
    if config.allowed_tasks_path:
        source_name = os.path.splitext(os.path.basename(config.allowed_tasks_path))[0]
    end = config.end_task if config.end_task is not None else "end"
    name = f"bad_tasks_{config.start_task}_{end}"
    if source_name:
        name += f"_in_{source_name}"
    return os.path.join(config.output_dir, name + ".jsonl")


def append_bad_task(path: str, scenario: str, task_id: int, reason: str, count: int,
                    type_: str, epoch: int | None = None, tool: str | None = None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "scenario": scenario,
        "task_id": task_id,
        "reason": reason,
        "type": type_,
        "count": count,
        "epoch": epoch,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if tool:
        entry["tool"] = tool
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
