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
        _warn_if_base_ignored(adapter_base, config.model_name, adapter_dir)
        if config.use_modelscope and not os.path.exists(adapter_base):
            logger.info(f"adapter 记录的 base 本地不存在，将从 ModelScope 下载：{adapter_base}")
            adapter_base = snapshot_download(adapter_base)
        elif not os.path.exists(adapter_base):
            # use_modelscope=False 时不会走上面那支，但 transformers 仍会把一个
            # 不存在的本地路径当成 HF repo id 联网拉取。这里提前说明，避免
            # 「明明关了下载却还在下模型」的困惑。
            logger.warning(
                f"adapter 记录的 base 路径不存在：{adapter_base}\n"
                "  transformers 会把它当成 HuggingFace repo id 联网下载；"
                "若想完全离线，请把该目录恢复到原路径，或改用 --output_dir 指向空目录。"
            )
        logger.info(f"实际加载的 base 模型：{adapter_base}")
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


def _same_model(a, b) -> bool:
    """判断两个模型标识是否指向同一处（兼容分隔符 / 末尾斜杠差异）。"""
    left, right = str(a), str(b)
    if left == right:
        return True

    def norm(p: str) -> str:
        return os.path.normpath(p).replace("\\", "/").rstrip("/")

    return norm(left) == norm(right)


def _warn_if_base_ignored(adapter_base, model_name, adapter_dir) -> None:
    """--model_name 被历史 adapter 静默覆盖时给出醒目警告。

    _find_adapter 会自动发现 output_dir 下的 checkpoint / lora_adapter；
    一旦命中，load_policy 用的就是 adapter_config.json 里记录的
    base_model_name_or_path，config.model_name 完全不参与。
    这个行为原本没有任何提示，很容易让人误以为「传了 --model_name 就换了
    base 模型」，实际却在加载（甚至联网下载）上一次训练用的 base。
    """
    if _same_model(adapter_base, model_name):
        return
    logger.warning(
        "检测到历史 LoRA adapter，本次训练将 **忽略 --model_name**：\n"
        f"    adapter 目录 : {adapter_dir}\n"
        f"    实际 base    : {adapter_base}\n"
        f"    --model_name : {model_name}   ← 已忽略\n"
        "  想改用新 base 从头训练，任选其一：\n"
        "    1) --output_dir 指向一个空目录（推荐，旧产物原样保留）\n"
        "    2) 把 <output_dir>/checkpoint 与 <output_dir>/lora_adapter 移走\n"
        "    3) 确实要续训，就用 --load_adapter 显式指定，别依赖自动发现"
    )
