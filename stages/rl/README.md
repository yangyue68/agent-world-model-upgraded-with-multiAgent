# stages/rl —— 进程内直调版 RL 训练 / 评估（方案2）

训练和评估默认从 `output/environment/gen_envs_ma.jsonl` 读取环境清单，通过各条记录的 `env_dir` 定位环境代码；可用 `--envs_path` 指定其他清单。




## 目录结构

```

当前训练代码按职责拆分为以下模块：

| 文件 | 职责 |
|---|---|
| `train_grpo.py` | 命令行入口，只负责解析参数并启动训练 |
| `config.py` | `Config` 配置项和 `ma_upgrade` 根路径 |
| `model_setup.py` | 加载 base model、tokenizer、LoRA 和断点 adapter |
| `data.py` | 读取 env/task/sample 产物，执行场景、任务白名单和范围过滤 |
| `preparation.py` | 正式训练前初始化模型、数据、judge、optimizer 和训练状态 |
| `rollout_runner.py` | 创建独立数据库环境、批量 rollout、调用 judge |
| `result_processing.py` | 过滤 env/judge 错误并把结果转换成 reward |
| `objectives.py` | 计算 GRPO advantage 和 policy log-prob |
| `checkpoint.py` | 保存 checkpoint、记录坏任务 |
| `training_loop.py` | 执行 epoch/task 循环和 policy 更新 |
stages/rl/
├── env.py         # InProcessEnv：进程内载入 env app + 直调路由
├── rollout.py     # rollout 采样循环
├── reward.py      # database diff + LLM judge 判分
├── train_grpo.py  # GRPO 训练主循环
├── eval.py        # 评估脚本
└── README.md      # 本文件
```

## 训练、评估命令

### 初次训练：

```bash
cd ma_upgrade
python -m stages.rl.train_grpo \
    --model_name Qwen/Qwen3-4B --use_modelscope True --device cuda \
    --allowed_tasks_path output/verifier/solvable_tasks.jsonl \
    --start_task 0 --end_task 50 \
    --checkpoint_every 5
```

> 参数说明：
>
> model_name：指定训练的base model
>
> use_modelscope: 为True时从魔塔社区下载，为False时从huggingface下载，建议为True
>
> `allowed_tasks_path` 指定任务名单：`output/verifier/rl_ready_tasks.jsonl` 包含 judge 已得出结论的全部任务；`solvable_tasks.jsonl` 包含至少一条 env rollout 被 judge 判为完成的任务；`unsolvable_tasks.jsonl` 包含所有已保存 rollout 均未完成的任务。可按这两组安排训练顺序。`gen_verifier_ma.jsonl` 保存详细评判结果，也可能包含 `judge_error`，不作为训练白名单。
>
> start_task、end_task：指定序号从start_task开始到end_task为止的任务为本次启动训练的任务（左闭右开），方便增量训练
>
> checkpoint_every：每训练checkpoint_every个任务保存一次lora权重，这是预防训练过程中意外中断、权重来不及保存
>
> 其他参数可以通过\stages\rl\train_grpo.py文件里的Config进行调整，也可以在启动命令时自己带上

### 训练产物：

训练结束后，lora权重保存在“stages\rl\output\checkpoint”目录下，该目录下保存每隔checkpoint_every个任务保存的lora权重。而且其中progress.json里记录了训练到哪个任务了，所以即使训练意外中断，也几乎不影响继续训练

### 评估命令

#### Baseline评估

```
python -m stages.rl.eval \
    --model_name Qwen/Qwen3-4B \
    --use_modelscope True \
    --allowed_tasks_path output/verifier/solvable_tasks.jsonl \
    --start_task 50 \
    --end_task 70\
    --num_samples 2
```

对于probe model能完成的任务（solvable_tasks.jsonl），未经RL训练的Qwen3-4B完成任务的通过率是：

#### 训练后的模型评估

```bash
python -m stages.rl.eval \
    --model_name stages/rl/output/checkpoint \
    --allowed_tasks_path output/verifier/solvable_tasks.jsonl \
    --start_task 50
    --end_task 70
    --num_samples 2
```

>参数说明：
>
>model_name：指定从哪里加载lora权重。但是不必再指定base model了,因为checkpoint目录里有adapter_config.json文件，该文件里指定了base model(与训练时的base model一致)
>
>allowed_tasks_path：含义与训练时的命令里的参数一致
>
>start_task、end_task：指定从allowed_tasks_path文件里的序号为start_task到end_task的任务作为本次eval的数据，end_task不传时默认到文件尾
>
>num_samples：每个任务执行num_samples多次，这是为了避免每个任务只执行一次时，可能因为偶然原因导致LLM本来能完成的任务却没能完成，减少噪声

### 增量训练

stages\rl\train_grpo.py程序能够自动检测checkpoint，如果checkpoint下存在lora权重，则自动将lora权重挂载(不是合并）到base model上进行增量训练。如果checkpoint目录或者其中内容不存在，则会重头初始化lora权重进行训练。所以增量训练与初次训练的命令是一样的，只需要根据checkpoint里的progress.json判断上次训练到哪里了，然后接着从那里训练即可。例如，上次训练到了unsolvable_tasks.jsonl文件的第200个任务，则增量训练可以从该文件第200个任务开始：

```
cd ma_upgrade
python -m stages.rl.train_grpo \
    --model_name Qwen/Qwen3-4B --use_modelscope True --device cuda \
    --allowed_tasks_path output/verifier/unsolvable_tasks.jsonl \
    --start_task 200 --end_task 300 \
    --checkpoint_every 5
```



### 合并模型权重

如果认为训练差不多了，就可以合并base model和checkpoint里的lora权重，得到最终LLM。命令：

```

```

结果：







## 其他关键参数说明

| 参数 | 默认 | 说明 |
|---|---|---|
| `--group_size` | 8 | 每个 task 采样 group_size 条 rollout（GRPO 组大小） |
| `--max_steps` | 15 | 每条 rollout 最多「生成→调工具」步数 |
| `--max_new_tokens` | 2048 | 单次生成最大 token（太小会导致LLM输出的tool_call参数被截断，LLM不可能完成任务，太大会纵容LLM一次输出太多，累积到后面成为历史记忆，造成seq_len变大，容易出现OOM(显存撑爆)） |

产物默认写到 `stages/rl/output/`（`trained_model/` 是 merge 后的完整权重，`lora_adapter/` 是本次训练结束时保存的 LoRA adapter，`checkpoint/` 是训练中途的 LoRA adapter 断点）。

## 增量训练（自动）

每次启动训练时，会自动检查 `stages/rl/output/lora_adapter/`：

- **若存在**（上次训练留下的 LoRA adapter）→ 自动 `merge_and_unload` 成完整权重 `trained_model/`，从它继续训练（重新加一个新的 LoRA adapter）；
- **若不存在** → 从头训练（加载 base 模型）。

也就是说：**连续多次启动 `train_grpo`，每次都会在上一次训练结果的基础上继续训**，无需手动 merge、无需指定 `--resume_from`。

> 注意：中间 checkpoint（`checkpoint/`）只作为训练中途的备份，不再用于断点续跑。中断后想继续，重新跑 `train_grpo` 即可（它会自动 merge 上次训练结束保存的 `lora_adapter`）。

## 多轮训练示例

典型用法是「分多次、每次训一段任务」，每次都在上一次结果上继续：

```bash
# 第 1 轮：从头训练任务 [0, 100)
python -m stages.rl.train_grpo \
    --model_name Qwen/Qwen3-4B --use_modelscope True --device cuda \
    --verifier_path stages/verifier/output/gen_verifier_ma.jsonl \
    --start_task 0 --end_task 100 --checkpoint_every 10

# 第 2 轮：自动 merge 第 1 轮的 lora_adapter，继续训练 [100, 200)
python -m stages.rl.train_grpo \
    --model_name Qwen/Qwen3-4B --use_modelscope True --device cuda \
    --verifier_path stages/verifier/output/gen_verifier_ma.jsonl \
    --start_task 100 --end_task 200 --checkpoint_every 10
```

要点：

- **无需手动 merge、无需 `--resume_from`**：每次启动自动把上一次的 `lora_adapter` merge 成 `trained_model` 再继续；
- `--start_task/--end_task` 控制本轮训哪一段任务（0-based、end 不含）；可搭配 `--allowed_tasks_path` 先按难易白名单过滤再切段；
- 每轮训练结束都把本轮结果存成新的 `lora_adapter/`，供下一轮自动继承。

---

## 单场景单任务查看轨迹

## 单独跑 linkedin task_4 + 看轨迹

**① 建一个只含这个任务的白名单文件**：

```bash
cd /root/autodl-tmp/ma_upgrade
mkdir -p stages/rl/temp
echo '{"scenario": "LinkedIn", "task_id": 4}' > stages/rl/temp/linkedin_task4.jsonl
```

**② 跑训练命令，加 `--verbose True`**：

```bash
cd ~/autodl-tmp/ma_upgrade

python -m stages.rl.train_grpo \
    --model_name Qwen/Qwen3-4B --use_modelscope True --device cuda \
    --allowed_tasks_path stages/rl/temp/linkedin_task4.jsonl \
    --group_size 2 \
    --verbose True \
    --num_epochs 1 \
    --checkpoint_every 0
```

## 方案对齐原论文

| 维度 | 选择 |
|---|---|
| 基础模型 | **Qwen3-4B**（原论文 Arctic-AWM-4B 的基础模型就是 `Qwen/Qwen3-4B`） |
| RL 算法 | **GRPO**（组内相对 reward，不需要 reward model） |
| reward | **当前 rollout 的 database diff + LLM judge**（RL 在线计算；verifier 输出只用于离线任务筛选） |
| 工具协议 | **XML 协议**（`<tool_call>`，复用 `awm/core/agent.py`） |
| 框架 | **自写轻量循环**（可读、可控，便于讲解） |

## 环境准备（GPU 服务器）

本地 `awm2` 环境只装了合成流水线的依赖，RL 训练需要额外的深度学习栈：

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install transformers accelerate peft modelscope
```

## 小规模 demo（不用白名单，直接按场景截取）

```bash
# 1 个场景 × 前 3 个任务 × 每组 4 条 rollout，用于快速验证链路
python -m stages.rl.train_grpo \
    --model_name Qwen/Qwen3-4B \
    --limit_scenarios 1 \
    --max_tasks 3 \
    --group_size 4 \
    --num_epochs 1
```

## 训练产物

| 产物 | 内容 | 用途 |
|---|---|---|
| `output_dir/lora_adapter/` | 本次训练结束时保存的 LoRA adapter | 下次训练自动 merge 成完整权重继续训 |
| `output_dir/trained_model/` | 自动 merge 后的完整权重 | eval 加载 |
| `output_dir/checkpoint/` | 训练中途的 LoRA adapter + `progress.json` | 中间备份（中断后可用它 eval，但不能续跑） |
| `output_dir/bad_tasks.jsonl` | 训练中被淘汰的坏任务清单（env 5xx / judge_error） | 训练后针对性修复 |

---

## 显存优化：六重治本方案（项目亮点）

在 24GB 显卡上训练 Qwen3-4B 的 GRPO 时，连续踩了多个「随 `seq_len` / step 数增长」的显存瓶颈。最终用六个「治本」手段分别解决——**不靠截断 agent 上下文，而是从算法层面消除无谓的显存占用**。

### 零、先补一点 LLM 底层（只讲够用的部分）

一个因果语言模型（如 Qwen3-4B）干的事就一句话：**给定前面一串 token，预测「下一个 token」是什么**。内部分四步：

```
输入 token 序列 [t1, t2, ..., tN]，形状：[batch_size,seq_len]
  ↓ ① Embedding：每个 token 转成一个 2560 维向量
  ↓ ② 过 36 层 Transformer：每层融合上下文，产出每个位置的「隐状态」hidden state（2560 维）
  ↓ ③ lm_head 线性层：把隐状态映射成「词典里每个词的分数」，这个分数叫 logits
  ↓ ④ softmax：把分数转成概率 P(下一个词)
```

其中第 ③ 步最关键：词典有 `vocab ≈ 151936` 个词，所以 lm_head 对**每个位置**输出一个 **151936 维**的分数向量。

再记住「训练」和「推理」的一个本质区别：

- **推理**（生成时）：只算前向拿结果，中间结果用完就扔；
- **训练**：要算「损失对参数的梯度」来更新参数（反向传播），所以前向的**中间结果（激活）必须保留**。

下面几个瓶颈，都来自「训练要保留中间结果」这件事，只是保留的东西不同。

> 记号：`seq_len` = agent 多轮工具调用后的完整上下文长度；`vocab ≈ 151936`；`layers = 36`；`group_size` = 每组 rollout 数；`max_steps` = 每条 rollout 步数。

### 前四重：治「训练更新阶段」的显存（四个瓶颈）

| # | 瓶颈 | 显存量级 | 一句话解释 |
|---|---|---|---|
| 1 | logits 张量 | `O(seq_len × vocab)` | 为拿「1 个目标词的分数」，算了「所有 15 万个词的分数」 |
| 2 | hidden_states 张量 | `O(seq_len × layers)` | `output_hidden_states=True` 把「36 层」全存下来，其实只用最后一层 |
| 3 | 前向激活 | `O(seq_len × layers)` | 反向传播要「36 层的中间结果」，全保留在显存 |
| 4 | step 计算图 | `O(group_size × max_steps)` | 所有 step 的 log_prob 计算图同时活着、统一 backward |

#### 方案一：分块 log-sum-exp（治「logits 张量」）

**目的**：消除「为取一个数而生成 1500 万个数的矩阵」的浪费。

`compute_log_prob` 要算「completion 每个 token 的 log 概率」。最直接的写法是 `logits = model(inputs).logits`，一次性生成 `[seq_len, 151936]` 的完整矩阵——`seq_len` 上万时，光这一个矩阵就 6GB+。

**关键洞察**：log 概率公式其实只需要「目标 token 的分数」和「所有 token 分数的 logsumexp」。前者可以用「最后一层 hidden_state 与目标词的 lm_head 行向量点积」直接算（分子），后者用「分块 logsumexp」避免一次性生成完整 logits。

**面试一句话**：「为取 log-softmax 里的一个数，不必生成完整的 vocab 维 logits——分子用目标点积，分母用分块 logsumexp，把显存从 `× vocab` 降到 `× chunk`。」

#### 方案二：只保留最后一层 hidden_states（治「hidden_states 张量」）

**关键洞察**：`output_hidden_states=True` 会把 36 层全存下来，但我们只要最后一层喂 lm_head——所以直接调内部 `Qwen3Model(...).last_hidden_state`，只保留一层，把 hidden_states 显存从乘 36 层降到乘 1 层。

**面试一句话**：「只留最后一层 hidden_state 喂 lm_head，把显存从 `× 36层` 降到 `× 1层`。」

#### 方案三：梯度检查点（治「前向激活」）

**关键洞察**：反向传播「需要」激活，但「不一定要保留」——**可以在反向时重新算一遍**。

```python
model.gradient_checkpointing_enable()   # 就这一行
# 前向不保留每层激活；反向走到某层时，从检查点重算那一层的前向
# → 激活显存 O(seq_len × 1层)，约降 36 倍
```

**效果**：激活显存降约 36 倍，代价是训练慢约 30~50%（时间换显存）。

**面试一句话**：「反向传播必须用前向的激活，但激活可以不存、反向时重算——用多算一遍前向的时间，换 36 层激活的显存。」

#### 方案四：逐步 backward（治「step 计算图累积」）

**关键洞察**：梯度是「可以逐步累加」的——`backward()` 会自动把梯度累加到参数上，所以「一次算一个 step 并立即 backward」和「全部累加后统一 backward」，**最终梯度结果完全一样**。

```python
optimizer.zero_grad()
total_loss = 0.0
for adv, p_ids, c_ids in steps:
    logp = compute_log_prob(model, p_ids, c_ids, config.device)
    step_loss = (-adv * logp) / n
    step_loss.backward()    # ← 立即反向，释放这个 step 的计算图
    total_loss += step_loss.item()
optimizer.step()
```

**效果**：计算图显存 `O(group_size × max_steps)` → `O(1 个 step)`。

**面试一句话**：「梯度可以逐步累加，每算一个 step 就立即 backward、释放计算图，和统一 backward 的梯度完全等价。」

#### 四者协同

| 方案 | 治的瓶颈 | 显存变化 | 代价 |
|---|---|---|---|
| 一：分块 log-sum-exp | logits 张量 | `× vocab` → `× 1024` | 无 |
| 二：只保留最后一层 hidden_states | hidden_states 张量 | `× 36层` → `× 1层` | 无 |
| 三：梯度检查点 | 前向激活 | `× 36层` → `× 1层` | 慢 30~50% |
| 四：逐步 backward | step 计算图 | `× N步` → `× 1步` | 无 |

实现位置都在 `stages/rl/train_grpo.py`：

- `compute_log_prob`：方案一（分块 log-sum-exp + 目标点积）+ 方案二（`raw_model.model(...).last_hidden_state` 只取最后一层）；
- `train` 里 `model.gradient_checkpointing_enable()`：方案三；
- `train` 的 GRPO 更新循环：方案四（逐步 backward）。

### 第五重治本：工具文档按需加载 + 三标签协议（从根源缩短 seq_len）

前四重治本都是「**容忍长 seq_len**」（让显存不被 seq_len 撑爆）。这一重反过来，**从根源把 seq_len 砍短**——seq_len 才是显存、速度、成本的总根源，而它的最大头是 `list_tools` 返回的完整参数 schema。

**问题**：原来的 `list_tools` 把每个工具的完整 inputSchema（参数名、类型、必填、默认值、枚举、嵌套 object）全部展开成文本，几十个工具 × 每个几十个参数 = 几千到上万 token。而 agent 调 `list_tools` 的目的只是「知道有哪些工具、大概能干什么」，用不到完整参数。

**方案一：工具文档按需加载**——`list_tools` 只返回「name + 一句话描述」，新增 `get_tool_detail_of(tool_name)` 按需返回单个工具完整 schema。效果：`list_tools` 从几千~上万 token 缩到几百 token。

**方案二：三标签协议**（每轮 LLM 恰好输出一种标签）：

| 标签 | 含义 | agent loop 动作 |
|---|---|---|
| `<tool_call>...</tool_call>` | 调用工具 | 解析并执行工具 |
| `<thinking>...</thinking>` | 简单推理（不调工具） | 回复 "continue" |
| `<final_answer>...</final_answer>` | 最终答案 | 结束 |

**方案三：轨迹顺序判分（防幻觉）**——第一个 tool_call 必须是 `list_tools`；每个新工具第一次 `call_tool` 前必须先 `get_tool_detail_of`；违规判 0 分。实现位置：`reward.py::check_tool_order`。

### 第六重治本：sub-batch 分片（治「采样阶段 batch × seq_len」）

这一重治**采样阶段**（`rollout_batch` 的 `model.generate`）——把 group_size 条轨迹塞进一个 batch 生成时，显存随 `batch × seq_len` 爆炸（KV cache、prefill logits、激活都随它线性增长，且 seq_len 被 pad 到组内最长）。

**关键洞察：按预算自动拆 batch**——要显存不超预算，就要求 `batch × max_len ≤ 预算`，移项即 `batch ≤ 预算 ÷ max_len`：

```python
sub_batch_size = max(1, max_tokens_per_batch // max(1, max_len))   # 当前 prompt 长度下最多塞几条
sub_batch_size = min(sub_batch_size, len(active))                  # 别超过现有条数
```

一句话：`batch × max_len` 永远被锁死在 `max_tokens_per_batch`（默认 9000，可调 `--max_tokens_per_batch`）以内，显存不再随 group_size 和 prompt 长度无上限增长。

**面试一句话**：「batch generate 的显存 ≈ batch × max_len，与其把 group_size 一降再降，不如用『显存预算 ÷ max_len』算出当前能并行几条，超了自动拆 sub-batch 依次生成。」

### 第七重治本：采样后 empty_cache（治「跨任务/跨条显存累积」）

前六重治本都是治「**单次计算的峰值显存**」。这一重治的是另一类：**峰值不高，但跑着跑着被慢慢吃满**——每条 rollout 的 `model.generate` 结束后，KV cache、prefill logits 等中间张量虽然已经「不被引用」，但还留在 PyTorch 的 CUDA 缓存池里，不还给驱动。串行跑几十上百条 rollout（训练几十个 task、评估上百个样本）后，这些空闲块累积起来，把 24GB 显存吃满，最后一条 rollout 连几百 MB 都分配不出来 → OOM。

**关键洞察：`torch.cuda.empty_cache()` 只释放「空闲块」，不碰被引用的 tensor**。而 rollout 采样是在 `torch.no_grad()` 下进行的（无计算图），采样结果又都转成了 CPU list / 字符串，所以采样结束后 GPU 上的 KV cache 等全是空闲块，可以安全释放，且**不影响后续反向传播**（反向传播的激活被计算图引用着，empty_cache 不会动它们）。

实现位置：

- `train_grpo.py`：`rollout_batch` 采样完成后、`compute_reward` 之前 `torch.cuda.empty_cache()`；
- `eval.py`：每条 rollout 的 `finally` 里 `torch.cuda.empty_cache()`。

**能省多少**：不是降低「单次采样峰值」，而是把显存占用从「峰值 + 跨任务累积」压回「单次采样峰值」。Qwen3-4B 的 KV cache 约 0.14MB/token，seq_len 几千 token 的单条采样会残留约 0.5~1.5GB 的 KV cache；不释放的话，训练 40 个 task、评估 100 个样本累积下来就是几十 GB（必然 OOM——你之前 eval 跑到 49/100 时 `free 27MB` 就是这个原因）。加了 empty_cache 后，无论训多少个 task / 评多少个样本，显存都恒定在「单次采样峰值」附近，**不再随任务数线性增长**。

**面试一句话**：「OOM 有两种：单次算太大（治峰值），和空闲块攒着不还（治累积）。`empty_cache` 治后者——把缓存池里没人引用的空闲块还回去，让显存恒定在峰值，而不是随任务数越涨越高。」

## 证明训练有效（held-out 对比）

1. 环境划分成不相交的 **train / eval** 两个集合（train 训练，eval 评估）。
2. 用同一个 eval 集，分别跑 **base Qwen3-4B** 和 **训练后的模型**，比「任务完成率」（verifier 判 complete 的比例）。
3. eval 集是训练时没见过的环境 → 证明泛化，而非背题。

```bash
# 评估 base 模型
python -m stages.rl.eval --model_name Qwen/Qwen3-4B --start_scenario N   # N=训练场景数，之后为 eval 集
# 评估训练后的模型
python -m stages.rl.eval --model_name stages/rl/output/trained_model --start_scenario N
```

（`eval.py` 已实现：逻辑同 rollout + code verifier 打分，只是不做反向传播。）
