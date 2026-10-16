"""把 LoRA adapter merge 成完整权重（trained_model），供评估 / 最终固化使用。

注意：train_grpo.py 现在只保存 checkpoint（不存 lora_adapter、也不自动 merge）。
本脚本用于「最终认为训练差不多了，才手动把 checkpoint 的 LoRA adapter 固化进 base」的场景。

用法（默认把 checkpoint merge 成 trained_model）：
  python -m stages.rl.merge_lora
  python -m stages.rl.merge_lora --adapter_path stages/rl/output/checkpoint --output_path stages/rl/output/trained_model
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import torch
from loguru import logger
from peft import PeftConfig, PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

# 定位 ma_upgrade 目录（merge_lora.py 上溯三级：rl → stages → ma_upgrade）
_MA_ROOT = Path(__file__).resolve().parent.parent.parent
# 项目根（部署目录）= ma_upgrade 的上级，用于 import ma_upgrade 包
_PROJECT_ROOT = _MA_ROOT.parent
for _p in (str(_MA_ROOT), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@dataclass
class Config:
    """merge 脚本配置。"""
    adapter_path: str = str(_MA_ROOT / "stages" / "rl" / "output" / "checkpoint")   # LoRA adapter 目录（训练只存 checkpoint）
    output_path: str = str(_MA_ROOT / "stages" / "rl" / "output" / "trained_model")   # merge 后完整权重目录
    device: str = "cuda"           # 设备：cuda → bf16 + GPU 上 merge；cpu → fp32 + CPU 上 merge
    use_modelscope: bool = True    # base 是 HF 名（如 Qwen/Qwen3-4B）时走魔搭下载


from lib.interrupt import run_cli, check_cancelled


def merge(config: Config):
    adapter = config.adapter_path
    out = config.output_path

    if not os.path.isdir(adapter):
        raise FileNotFoundError(f"adapter 目录不存在：{adapter}")

    # base 模型路径记录在 adapter_config.json 里（train_grpo 用魔搭下载时是本地缓存路径）
    base = PeftConfig.from_pretrained(adapter).base_model_name_or_path
    logger.info(f"base 模型路径：{base}")

    # base 路径不存在（说明记录的是 HF 名，如 Qwen/Qwen3-4B）→ 走魔搭下载
    if config.use_modelscope and not os.path.exists(base):
        from modelscope import snapshot_download
        logger.info(f"base 路径不存在，从 ModelScope 下载：{base}")
        base = snapshot_download(base)

    dtype = torch.bfloat16 if config.device.startswith("cuda") else torch.float32
    logger.info(f"加载 base 模型（dtype={dtype}）...")
    model = AutoModelForCausalLM.from_pretrained(base, dtype=dtype)
    check_cancelled()

    logger.info(f"挂 LoRA adapter：{adapter}")
    model = PeftModel.from_pretrained(model, adapter)

    # 搬到目标设备再 merge（GPU 上 bf16 matmul 更稳；CPU 上 bf16 运算支持有限）
    model.to(config.device)

    logger.info("merge_and_unload ...")
    model = model.merge_and_unload()
    check_cancelled()

    logger.info(f"保存完整权重到 {out}")
    model.save_pretrained(out)
    AutoTokenizer.from_pretrained(base).save_pretrained(out)
    logger.success(f"完成：{out}")


def main():
    from simpleArgParser import parse_args
    config = parse_args(Config)
    merge(config)

if __name__ == "__main__":
    run_cli(main)
