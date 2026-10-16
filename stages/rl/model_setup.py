# -*- coding: utf-8 -*-
"""加载 RL policy、tokenizer 和 LoRA adapter。"""

import os

import torch
from loguru import logger

from stages.rl.config import Config


def load_policy(config: Config):
    """加载基础模型，并按配置创建或恢复 LoRA。"""

    from transformers import AutoModelForCausalLM, AutoTokenizer

    if config.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError(
            "启动失败：--device 指定了 cuda，但当前环境没有可用的 NVIDIA GPU。"
            "请先运行 nvidia-smi 确认 GPU 可见；若确实无 GPU，改用 --device cpu。"
        )

    dtype = torch.bfloat16 if config.device.startswith("cuda") else torch.float32
    base_model_path = config.model_name
    snapshot_download = None
    if config.use_modelscope:
        from modelscope import snapshot_download as _snapshot_download

        snapshot_download = _snapshot_download
        base_model_path = snapshot_download(config.model_name)
        logger.info(f"已从 ModelScope 下载模型到 {base_model_path}")

    adapter_dir = _find_adapter(config)
    if adapter_dir:
        from peft import PeftConfig, PeftModel

        adapter_base = PeftConfig.from_pretrained(adapter_dir).base_model_name_or_path
        if config.use_modelscope and not os.path.exists(adapter_base):
            adapter_base = snapshot_download(adapter_base)
        model = AutoModelForCausalLM.from_pretrained(adapter_base, dtype=dtype)
        model = PeftModel.from_pretrained(model, adapter_dir)
        tokenizer = AutoTokenizer.from_pretrained(adapter_base)
        logger.info(f"从 {adapter_dir} 加载 LoRA adapter，base={adapter_base}")
    else:
        model = AutoModelForCausalLM.from_pretrained(base_model_path, dtype=dtype)
        tokenizer = AutoTokenizer.from_pretrained(base_model_path)
        if config.use_lora:
            from peft import LoraConfig, TaskType, get_peft_model

            lora_config = LoraConfig(
                task_type=TaskType.CAUSAL_LM,
                r=config.lora_r,
                lora_alpha=config.lora_alpha,
                lora_dropout=config.lora_dropout,
                target_modules=[
                    "q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj",
                ],
                bias="none",
            )
            model = get_peft_model(model, lora_config)
            model.print_trainable_parameters()

    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    tokenizer.padding_side = "left"
    model.to(config.device)
    model.train()
    model.gradient_checkpointing_enable()
    return model, tokenizer


def _find_adapter(config: Config) -> str | None:
    """按显式参数、checkpoint、旧 lora_adapter 的顺序选择恢复点。"""

    if config.load_adapter:
        return config.load_adapter
    checkpoint_dir = os.path.join(config.output_dir, "checkpoint")
    if os.path.isdir(checkpoint_dir):
        return checkpoint_dir
    lora_adapter_dir = os.path.join(config.output_dir, "lora_adapter")
    if os.path.isdir(lora_adapter_dir):
        return lora_adapter_dir
    return None
