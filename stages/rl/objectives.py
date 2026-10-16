# -*- coding: utf-8 -*-
"""GRPO 的 advantage 和 policy log-prob 计算。"""

import torch


def compute_group_advantages(rewards: list[float]) -> list[float]:
    rewards_tensor = torch.tensor(rewards, dtype=torch.float32)
    return ((rewards_tensor - rewards_tensor.mean()) / (rewards_tensor.std() + 1e-8)).tolist()


def compute_log_prob(model, prompt_ids: list[int], completion_ids: list[int], device: str,
                     chunk_size: int = 8192) -> torch.Tensor:
    """计算 completion 的总 log-prob，按词表分块避免一次展开完整 logits。"""

    if not completion_ids:
        return torch.tensor(0.0, device=device, requires_grad=True)

    full_ids = prompt_ids + completion_ids
    inputs = torch.tensor([full_ids], device=device)
    raw_model = model.base_model.model if hasattr(model, "base_model") else model
    lm_head_weight = raw_model.lm_head.weight
    hidden_states = raw_model.model(inputs).last_hidden_state[0]

    count = len(completion_ids)
    positions = list(range(len(prompt_ids) - 1, len(prompt_ids) - 1 + count))
    target_ids = torch.tensor(completion_ids, device=device)
    target_hidden = hidden_states[positions]
    target_logits = (target_hidden * lm_head_weight[target_ids]).sum(dim=-1)

    logsumexp = torch.full((count,), float("-inf"), device=device)
    for start in range(0, lm_head_weight.shape[0], chunk_size):
        end = min(start + chunk_size, lm_head_weight.shape[0])
        logits = target_hidden @ lm_head_weight[start:end].T
        maximum = torch.maximum(logsumexp, logits.max(dim=-1).values)
        logsumexp = maximum + torch.log(
            torch.exp(logsumexp - maximum)
            + torch.exp(logits - maximum.unsqueeze(-1)).sum(dim=-1)
        )
    return (target_logits - logsumexp).sum()
