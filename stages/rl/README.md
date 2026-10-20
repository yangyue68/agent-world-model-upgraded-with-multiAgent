# stages/rl — GRPO 训练与评估

本目录在 `ma_upgrade` 生成的任务环境上，用组内相对策略优化训练 Qwen3-4B。训练读取 `output/environment/gen_envs_ma.jsonl` 中的环境，结合 task、sample 和 verifier 产物构造训练任务，在线执行 rollout 并用数据库 diff 与 LLM judge 打分。

## 目录结构

| 文件 | 职责 |
|---|---|
| `train_grpo.py` | 命令行入口，解析参数并启动训练 |
| `config.py` | `Config` 配置项和 `ma_upgrade` 根路径 |
| `model_setup.py` | 加载 base model、tokenizer、LoRA 和续训 adapter |
| `data.py` | 读取 task/db/sample/verifier/env 产物并构建训练任务 |
| `preparation.py` | 初始化模型、数据、judge、optimizer 和训练状态 |
| `rollout_runner.py` | 创建独立数据库环境，批量执行 rollout 并调用 judge |
| `rollout.py` | 采样循环、工具调用解析和消息管理 |
| `reward.py` | 数据库 diff、轨迹顺序检查和 LLM judge 判分 |
| `result_processing.py` | 过滤环境与 judge 错误，把结果转换成奖励 |
| `objectives.py` | 计算 advantage 和 policy log-prob |
| `training_loop.py` | epoch/task 循环、梯度裁剪和策略更新 |
| `checkpoint.py` | 保存 checkpoint 和坏任务记录 |
| `eval.py` | 评估脚本 |
| `merge_lora.py` | 合并 LoRA adapter 与基础模型 |

## 算法原理

训练使用组内相对策略优化（GRPO）。对每个任务采样一组 `group_size` 条轨迹，先计算奖励，再把奖励转成组内相对优势：

```text
mean = 组内奖励均值
std = 组内奖励标准差
advantage_i = (reward_i - mean) / (std + 1e-8)
```

如果组内奖励几乎没有方差，说明这个任务的组内比较失去信息量。代码回退到全局 baseline：

```text
advantage_i = reward_i - global_baseline
```

全局 baseline 由历史任务奖励的滑动平均维护。这样分数都偏高的任务会整体获得正优势，分数都偏低的任务会整体获得负优势。

policy update 使用 log-prob 目标：

```text
loss = -sum_steps( advantage * log_prob ) / number_of_steps
```

其中 `log_prob` 是当前策略对 rollout 中每段 assistant completion token 的 log 概率。当前实现是 on-policy 的 REINFORCE 风格更新，没有保存生成时的旧策略 log-prob，因此没有 PPO/GRPO 的 ratio clip。为了避免单步把模型推得过远，训练加了两个保护：

1. 梯度裁剪：每次 `optimizer.step()` 前执行 `clip_grad_norm_(model.parameters(), max_grad_norm)`，默认 `1.0`。
2. 较低学习率：默认 AdamW 学习率从 `1e-3` 降到 `1e-4`。

这两个设置直接限制参数更新幅度，降低长轨迹和大 advantage 导致的策略突变风险。

## 训练流程

```text
读取任务与环境
    ↓
为每个 rollout 创建独立数据库副本
    ↓
模型批量生成轨迹，调用 FastAPI 工具
    ↓
数据库 diff + LLM judge 计算奖励
    ↓
过滤环境错误和 judge 错误
    ↓
计算组内 advantage 或全局 baseline 回退
    ↓
逐条反向传播，累计梯度
    ↓
梯度裁剪 + AdamW 更新
    ↓
保存 checkpoint / 坏任务记录
```

单条轨迹内部是多轮 agent loop：模型先输出 `<tool_call>`，程序调用环境接口，把结果作为 tool/user 消息返回；模型输出 `<final_answer>` 后结束。任务完成与否由 reward 阶段根据数据库变化、轨迹顺序和 LLM judge 判断。

## 训练命令

```bash
cd ma_upgrade/agent-world-model-upgraded-with-multiAgent

python -m stages.rl.train_grpo \
    --model_name /root/autodl-tmp/ms-cache/models/Qwen3-4B-Instruct-2507 \
    --use_modelscope False \
    --device cuda \
    --allowed_tasks_path output/verifier/rl_ready_tasks.jsonl \
    --judge_model Qwen3-Coder-30B-A3B-Instruct \
    --judge_provider openai \
    --group_size 10 \
    --num_epochs 3 \
    --max_steps 15 \
    --max_new_tokens 30000 \
    --max_tokens_per_batch 96000 \
    --learning_rate 1e-4 \
    --max_grad_norm 1.0 \
    --checkpoint_every 5
```

常用参数：

| 参数 | 说明 |
|---|---|
| `model_name` | base model 本地路径或模型 ID |
| `use_modelscope` | `True` 从 ModelScope 下载；`False` 使用本地路径或 HuggingFace |
| `allowed_tasks_path` | verifier 产出的可训练任务白名单 |
| `start_task` / `end_task` | 训练任务切片，左闭右开 |
| `group_size` | 每个任务的 rollout 数量；正式训练建议 ≥ 10 |
| `temperature` | rollout 采样温度，训练建议保持在 `1.0` 左右以保留探索 |
| `learning_rate` | AdamW 学习率；当前默认 `1e-4`，建议从 `1e-4` 或 `5e-5` 开始 |
| `max_grad_norm` | 梯度范数上限；默认 `1.0` |
| `num_epochs` | 训练轮数 |
| `checkpoint_every` | 每处理多少个任务保存一次 checkpoint |
| `judge_model` / `judge_provider` | LLM judge 的模型和提供方 |
| `enable_thinking` | 被训练模型是否开启 Qwen3 thinking；默认关闭 |

注意：`model_setup.py` 会按 `--load_adapter`、`output_dir/checkpoint`、`output_dir/lora_adapter` 的顺序查找已有 adapter。如果要重新从 base model 开始训练，请先清空或改名 `stages/rl/output/checkpoint`，或使用新的 `--output_dir`。

## 训练产物

| 产物 | 内容 |
|---|---|
| `stages/rl/output/checkpoint/` | 中途和结束时的 LoRA adapter，包含 `adapter_config.json` |
| `stages/rl/output/bad_tasks.jsonl` | 训练中因环境 5xx 或 judge 错误淘汰的任务 |
| `progress.json` | checkpoint 内的训练进度记录，便于中断恢复 |

## 评估命令

```bash
python -m stages.rl.eval \
    --model_name /root/autodl-tmp/ms-cache/models/Qwen3-4B-Instruct-2507 \
    --allowed_tasks_path output/verifier/rl_ready_tasks.jsonl \
    --start_task 400 \
    --num_samples 2 \
    --num_workers 10 \
    --judge_model Qwen3-Coder-30B-A3B-Instruct \
    --judge_provider openai
```

评估训练后模型时，把 `--model_name` 指向 checkpoint，例如：

```bash
python -m stages.rl.eval \
    --model_name stages/rl/output/checkpoint \
    --allowed_tasks_path output/verifier/rl_ready_tasks.jsonl \
    --start_task 400 \
    --num_samples 5
```

评估会执行 rollout、调用 LLM judge 并统计完成率。比较训练前后效果时，建议使用同一批任务、相同的 `num_samples` 和随机种子；`num_samples` 越大，结果越稳定。

## 奖励机制

奖励阶段综合三类信息：

1. 数据库 diff：通过比较初始数据库和最终数据库，判断任务是否产生了期望状态变化。
2. 轨迹顺序：检查是否优先调用 `list_tools`、调用 `call_tool` 前是否先获取工具详情，以及是否存在格式违规。
3. LLM judge：结合任务、轨迹、数据库初始状态和 diff 判断任务是否完成。

最终分类包括 `complete`、`ordered`、`partial`、`agent_error`、`env_error` 和 `judge_error`。环境错误和 judge 错误不作为模型奖励信号，会在训练中被过滤或记录为坏任务。

## 显存优化说明

训练循环使用四个主要手段控制显存：

1. 分块 log-sum-exp，避免一次性生成完整词表 logits。
2. 只保留最后一层 hidden states，用于目标 token 的 log-prob 计算。
3. `gradient_checkpointing_enable()` 用重算前向激活换取显存。
4. 逐条 `backward()` 释放计算图，而不是把整个组的完整计算图一起保留。

rollout 阶段还会按 `max_tokens_per_batch` 自动拆分子批次，并在每次 rollout 后调用 `torch.cuda.empty_cache()` 清理 CUDA 缓存池。

## 训练效果验证

建议在互不相交的任务切片上做 held-out 对比：

```bash
# base model
python -m stages.rl.eval --model_name Qwen/Qwen3-4B-Instruct-2507 --start_task 400

# 训练后模型
python -m stages.rl.eval --model_name stages/rl/output/checkpoint --start_task 400
```

比较指标应至少包括任务语义完成率、严格协议完成率、平均奖励和工具顺序违规率。只有统一口径后仍出现下降，才需要继续分析学习率、训练轮数和 advantage 分布。
