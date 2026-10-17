# Agent World Model —— 多 Agent 升级版（ma_upgrade）

本项目是 [Snowflake-Labs/agent-world-model](https://github.com/Snowflake-Labs/agent-world-model)（**Agent World Model**，ICML 2026，arXiv 2602.10090）的**二次开发**：在原合成环境代码基础上，把原来「单次 LLM 生成、凑够数量就收、简单出错重试」的流水线，升级为「**多 agent 协作 + 规则校验 + 语义评审 + 真实执行闭环**」的高质量合成流水线，并补上了从「环境合成」到「GRPO 强化学习训练」的完整闭环。

## ⚖️ 来源与许可声明（重要）

- 本项目**基于** [Snowflake-Labs/agent-world-model](https://github.com/Snowflake-Labs/agent-world-model) 二次开发，原始版权归原作者所有：
  Zhaoyang Wang, Canwen Xu, Boyi Liu, Yite Wang, Siwei Han, Zhewei Yao, Huaxiu Yao, Yuxiong He（UNC-Chapel Hill & Snowflake AI Research）。
- 本仓库只包含 `ma_upgrade/` 多 agent 升级部分；原始 `awm/` 代码请到上游 [Snowflake-Labs/agent-world-model](https://github.com/Snowflake-Labs/agent-world-model) 仓库获取。
- 使用本项目请务必引用原论文（见文末 [Citation](#citation)），并保留本声明。

## 目录结构

```
ma_upgrade/
├── README.md                 # 本文件
├── lib/                      # 通用基础模块：gpt 客户端 / orchestrator 编排器 / tools 工具                                 / runtime 运行时
├── stages/                   # 合成流水线各阶段（核心，共10个阶段，见下）
└── requirements.txt          # 依赖清单
```

## 合成 + 训练流水线（10 个阶段）

所有阶段入口均为 `stages/<stage>/generate.py`，用 `python -m stages.<stage>.generate ...` 运行（在 `ma_upgrade/` 目录下）。每个阶段都遵循「**生成 → 规则校验（确定性）→ 语义评审（LLM）→ 真实执行 → fixer 修复**」的统一范式，具体到各个阶段，略有差别，详见各个阶段下的更详细的README文档。

| # | 阶段 | 目录 | 产物 |
|---|---|---|---|
| 1 | 场景生成 | `stages/scenario` | `gen_scenario_ma.jsonl` |
| 2 | 任务角度规划 | `stages/angle` | `gen_angles_ma.jsonl` |
| 3 | 任务生成 | `stages/task` | `gen_tasks_ma.jsonl` |
| 4 | 数据库表结构生成 | `stages/db` | `gen_db_ma.jsonl` + `.db` 文件 |
| 5 | 示例数据生成 | `stages/sample` | `gen_sample_ma.jsonl` |
| 6 | API 规范生成 | `stages/spec` | `gen_spec_ma.jsonl` |
| 7 | 环境代码生成 | `stages/environment` | `output/environment/gen_envs_ma.jsonl` + 单文件环境代码 |
| 8 | LLM judge 评判 env 轨迹完成情况 | `stages/verifier` | `gen_verifier_ma.jsonl`, `rl_ready_tasks.jsonl`, `solvable_tasks.jsonl`, `unsolvable_tasks.jsonl` |
| 9 | RL training / evaluation | `stages/rl` | `trained_model` / `checkpoint` |

各阶段的详细说明见 `stages/<stage>/README.md`

### 中断运行（PowerShell / Linux）

各生成阶段及 RL 训练、评估入口均通过 `lib/interrupt.py` 处理取消，原来的启动命令无需调整。

- 按一次 **Ctrl+C**：停止提交新任务、取消排队任务和正在等待的 LLM 请求，关闭环境连接并清理本次启动的服务子进程。取消不会记作任务不合格，也不会进入 LLM 错误重试。
- 默认留出 **5 秒清理时间**。如果第三方调用仍然阻塞，程序会清理已登记的子进程并强制退出；再次按 **Ctrl+C** 会提前触发强制退出。取消的退出码为 `130`，PowerShell 可用 `$LASTEXITCODE` 查看。
- 已经写入的阶段产物和训练 checkpoint 可继续使用；正在处理但尚未落盘的结果不保证保存。RL 中断不会自动保存新的 checkpoint。强制退出可能留下当前任务的临时文件。
- 修改只对重新启动的 Python 进程生效。后续新增命令行入口也应使用 `run_cli(main)`，并使用本模块提供的线程池和 `as_completed`；单纯导入模块不会安装信号处理器。

离线验证：在项目根目录运行 `python -m unittest lib.test_interrupt -v`。Windows 测试会在隐藏的独立控制台发送真实 Ctrl+C 事件，不请求 LLM，也不使用正式数据库。

## 核心设计

### 两层验证

| 层 | 谁来做 | 特点 | 验证什么 |
|---|---|---|---|
| 规则校验 | 各阶段 `stages/*/validators.py`（纯 Python） | 快、免费、100% 准确 | 确定性约束（去重 / 黑名单 / 格式 / 外键 / 认证字段等） |
| 语义评审 | reviewer agent（LLM） | 理解语义 | 具体性、真实性、任务覆盖、外键一致性 |

原则：**能程序化验证的绝不让 LLM 评**（省钱、稳定）。



### 各阶段统一范式

```
generator 从零生成 → 规则校验 / reviewer语义评审 / 真实执行报错 统一收集成 problems
→ fixer 定向修复 → 循环，直到零错误 / 评审通过
```

- generator 只在第一轮冷启动，之后所有问题都交给 fixer 定向修复（不重新全量生成）。
- fixer 只返回「改动的表/文件」，好表/好文件原样保留（省 token、避免失控）。



## 运行方式

1. 克隆或者下载本项目到您的计算机，进入 `ma_upgrade/` 目录。

2. 创建conda环境，安装依赖：

   ```
   conda create -n awm python=3.12 -y
   
   pip install -r requirements.txt
   ```

3. 设置 LLM 环境变量（Azure OpenAI 或 OpenAI 兼容接口二选一）：

```bash
# 方式 A：Azure OpenAI
export AWM_SYN_LLM_PROVIDER=azure
export AZURE_ENDPOINT_URL=https://xxx.openai.azure.com
export AZURE_OPENAI_API_KEY=你的apiikey
export AWM_SYN_OVERRIDE_MODEL=你的模型名

# 方式 B：OpenAI 兼容接口（如 DeepSeek）
export AWM_SYN_LLM_PROVIDER=openai
export OPENAI_BASE_URL=https://api.deepseek.com/v1（如果您使用其他LLM,此处相应更改）
export OPENAI_API_KEY=你的key
export AWM_SYN_OVERRIDE_MODEL=你选择进行各阶段产物合成的LLM(例如deepseek-v4-pro)
```

设置embedding模型（只有第一阶段场景生成需要使用）：

```bash
$env:EMBEDDING_OPENAI_API_KEY  = 你的apikey
$env:EMBEDDING_OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:EMBEDDING_OPENAI_MODEL    = "text-embedding-v4"    
```

> 注：虽然本项目使用多agent方式进行了优化，例如env阶段，把原来“发送一个prompt、让LLM一口气生成整个环境代码（数千行甚至上万行）”的方式改成了“planner先生产文件清单、generator再生产具体的每个文件”，降低了LLM出错的概率并降低了对LLM单次输出长度的要求，但是仍然推荐使用deepseek-v4-pro这种输出能力强的LLM（384k tokens，相当于约100万个字符），不推荐使用qwen系列模型（输出的最大长度是32768 tokens，相当于10万个字符）。
>
> 这是因为如果LLM一次输出的文件超过了32768 个tokens，文件内容被直接截断，必然不能通过规则校验和语义评审，要重新尝试，但是LLM并不能一边输出一边数自己吐了多少token，第二次尝试依然会犯同样错误，所以导致成功率低。总之，max_tokens长一点总比短一点好。例如本人选了deepseek-v4-pro，则max_tokens可以设为380000：
>
> ```
> "generator": Agent(
>             "scenario_generator", SCENARIO_GENERATION_SYSTEM_PROMPT,
>             client=GPTClient(timeout=600), model=config.model,
>             temperature=config.temperature, max_tokens=380000,
>         )
> ```
>
> 并不代表LLM真的一次就要输出380000个tokens，380000只是规定了输出上限

 

5.按流水线顺序依次运行各阶段（以 1~4 阶段为例，此处展示不全，详见各个阶段的README文档）：

```bash
python -m stages.scenario.generate --input tasks/...
python -m stages.angle.generate ...
python -m stages.task.generate --input_scenario ... --input_angles ...
python -m stages.db.generate --input stages/task/output/gen_tasks_ma.jsonl
# ... 依此类推到 sample / spec / env / verifier
```

  

## 私有化部署（可选）

如果不想依赖云端 API（DeepSeek / 阿里云 DashScope 等），可以在自己的 GPU 服务器（推荐 ≥ 96GB 显存）上私有化部署 **生成模型（LLM）** 和 **embedding 模型**。下面以「DeepSeek-Coder-V2-Lite（生成）+ BAAI/bge-m3（embedding）+Qwen3-30B-A3B-Instruct-2507-FP8（裁判）」为例，均部署成 OpenAI 兼容 API，与现有代码无缝对接。

下面的演示基于autodl服务器，注意，本人在最初尝试时踩了很多坑，建议您使用遵照下面几个版本：

1、python=3.12，在创建conda环境时确定：conda create -n awm python=3.12 -y

2、cuda版本：不要选择12.8，最好直接使用13.0，可以在autodl控制台进行升级

3、弄清楚你部署的私有化模型的最长输出长度，我选择的DeepSeek-Coder-V2-Lite模型的输出max_tokens=120k，我把各个阶段的各个agent的max_tokens设成了120000

### 1. 私有化部署 LLM（各阶段产物生成）

**主要生产模型**：`deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct`
（MoE 16B 总参数 / 2.4B 激活，128K 上下文，BF16 约 32GB，开源可商用）

**Embedding模型**：`BAAI/bge-m3`内快），只在第一阶段（senario生成阶段使用）

```
#ModelScope 缓存指向数据盘
export MODELSCOPE_CACHE=~/autodl-tmp/ms-cache
echo 'export MODELSCOPE_CACHE=~/autodl-tmp/ms-cache' >> ~/.bashrc
source ~/.bashrc

#激活conda环境
conda activate awm
# 只下到缓存（不加 --local_dir，单份）
modelscope download --model deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct
```



**② 部署 vLLM**：

```bash
#激活环境
conda activate awm 

# 安装
pip install vllm

# 启动 OpenAI 兼容服务
vllm serve /root/autodl-tmp/ms-cache/models/deepseek-ai--DeepSeek-Coder-V2-Lite-Instruct/snapshots/master \
  --served-model-name DeepSeek-Coder-V2-Lite-Instruct \
  --host 0.0.0.0 \
  --port 6008 \
  --tensor-parallel-size 1 \
  --max-model-len 130000 \
  --gpu-memory-utilization 0.90 \
  --max-num-seqs 12 \
  --enable-auto-tool-choice \
  --tool-call-parser deepseek_v3
```



参数说明：
- `--max-model-len 131072`：128K 上下文（128×1024）
- `--served-model-name`：API 里使用的模型名（下面环境变量里对应）
- `--gpu-memory-utilization 0.9`：显存利用率 90%，留 10% 余量防 OOM
- `--max-num-seqs 32`：并发序列数（并发高或显存紧张就调小）

**③ 验证服务**：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-coder-v2-lite","messages":[{"role":"user","content":"写一个冒泡排序"}],"max_tokens":100}'
```

**④ 配置环境变量**（无需改代码，`lib/gpt.py` 已支持）：

```bash
export AWM_SYN_LLM_PROVIDER=openai
export OPENAI_BASE_URL=https://uu808973-v48d-02b512d1.westb.seetacloud.com:8443
export OPENAI_API_KEY=EMPTY              # vLLM 不校验 key，给个非空值即可
export AWM_SYN_OVERRIDE_MODEL=DeepSeek-Coder-V2-Lite-Instruct   
```

powershell里配置环境变量：

```
$env:AWM_SYN_LLM_PROVIDER="openai"
$env:OPENAI_BASE_URL="https://uu808973-826x-9b3b8f6d.westd.seetacloud.com:8443/v1"
$env:OPENAI_API_KEY="dummy"              # vLLM 不校验 key，给个非空值即可
$env:AWM_SYN_OVERRIDE_MODEL="DeepSeek-Coder-V2-Lite-Instruct"   
```

ssh -p 28491 root@connect.westb.seetacloud.com



⑤ max_tokens 调整（重要）**：

128K 上下文 = **输入 + 输出**。DeepSeek-Coder-V2-Lite 推荐把各阶段 `build_agents` 里的 `max_tokens` 从 `380000` 改为 **`100000 ~ 120000`**（而非 380000），并保证单次 prompt 输入不超过约 11K token，否则「输入 + 输出 > 128K」会被截断。涉及文件：`stages/{scenario,task,db,sample,env,verifier}/generate.py`。

### 2. 私有化部署 embedding 模型（scenario 阶段去重）

**模型**：`BAAI/bge-m3`（多语言、1024 维、Apache 2.0，适合中英文场景去重）

**① 下载**（与 LLM 一样只下到缓存、单份；缓存目录已在上面指向数据盘 `~/autodl-tmp/ms-cache`）：

```bash
# 只下到缓存（不加 --local_dir，单份，与 LLM 共用同一个缓存目录）
modelscope download --model BAAI/bge-m3
```

模型实际路径：`~/autodl-tmp/ms-cache/models/BAAI--bge-m3/snapshots/master`

**② 部署**（vLLM embedding 模式 或 TEI 二选一）：

```bash
# 方式 A：vLLM（与 LLM 统一）
vllm serve ~/autodl-tmp/ms-cache/models/BAAI--bge-m3/snapshots/master \
    --served-model-name bge-m3 \
    --task embed \
    --host 0.0.0.0 \
    --port 8001

# 方式 B：TEI（HuggingFace 专用 embedding 服务）
docker run --gpus all -p 8001:80 \
    -v ~/autodl-tmp/ms-cache/models/BAAI--bge-m3/snapshots/master:/data \
    ghcr.io/huggingface/text-embeddings-inference:latest \
    --model-id /data
```

**③ 配置环境变量**：

```bash
export EMBEDDING_OPENAI_API_KEY=EMPTY
export EMBEDDING_OPENAI_BASE_URL=http://127.0.0.1:8001/v1
export EMBEDDING_OPENAI_MODEL=bge-m3
```

**④ 验证**：

```bash
curl http://127.0.0.1:8001/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-m3","input":["hello world"]}'
```

> 注意：`stages/scenario/generate.py` 里 `embedding_dim` 硬编码为 `3072`（OpenAI text-embedding-3-large 的维度），bge-m3 实际是 `1024` 维。该值仅用于「空矩阵占位」，不影响实际运行（第一次写入池子时会被真实维度覆盖），但若追求严谨，可把该值改成 `1024`。

### 配置训练时judge

下载模型：

```
modelscope download \
  --model Qwen/Qwen3-30B-A3B-Instruct-2507-FP8 \
  --local_dir /root/autodl-tmp/ms-cache/models/Qwen3-30B-A3B-Instruct-2507-FP8
```



启动运行：

```
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0
unset OMP_NUM_THREADS

vllm serve /root/autodl-tmp/ms-cache/models/Qwen3-30B-A3B-Instruct-2507-FP8 \
  --served-model-name Qwen3-30B-A3B-Instruct-2507-FP8 \
  --host 0.0.0.0 \
  --port 6008 \
  --tensor-parallel-size 1 \
  --max-model-len 130000 \
  --gpu-memory-utilization 0.90 \
  --max-num-seqs 14 \
  --max-num-batched-tokens 32768 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml
```

参数说明：

> port:最好设成6008或6006，这两个端口是autodl官方向外提供的公网可以访问的地址
>
> tensor-parallel-size：并行运行的GPU数量，表示把计算任务分配到多少张GPU上，如果租的服务器只有一张可用的GPU，就设为1
>
> max-model-len ：支持的单次请求的上下文总长度（模型的输入+输出），Qwen3-30B-A3B-Instruct-2507-FP8 是128k（131072）个tokens，我在此处设成130000
>
> gpu-memory-utilization：分配这么大比例的显存给本次启动的LLM，只要这次启动运行的进程没有被停止 ，那么就算客户端没有发请求，这部分显存还是被占用
>
> max-num-seqs: 服务端一次并发处理的请求数
>
> max-num-batched-tokens 32768：**一个批次**（多条请求加起来）一次能处理的 token 总量
>
> 为什么“单条 130000”能大于“批次 32768”？
>
> 因为这两个数字**比较的对象不同**：
>
> - `max-model-len = 130000`：说的是**一条请求本身可以有 13 万 tokens**。
> - `max-num-batched-tokens = 32768`：说的是**一个批次里所有请求加起来，一次只算 32768 tokens**。
>
> **它们不冲突。** 当一条 13 万 tokens 的请求进来时，vLLM 不会一次算完，而是**分批处理**：
>
> ```
> 请求 A：130000 tokens
> 批次 1：算前 32768 tokens
> 批次 2：算接下来的 32768 tokens
> 批次 3：算接下来的 32768 tokens
> 批次 4：算剩下的 31696 tokens
> ```
>
> 这就是 **chunked prefill（分块预填充）**——长请求被自动拆成多个小批次，逐块算。
>
> 🔄 换个类比
>
> | 概念                           | 类比                        |
> | :----------------------------- | :-------------------------- |
> | `max-model-len 130000`         | 一封信最多 13 万字          |
> | `max-num-batched-tokens 32768` | 打印机一次最多打印 3.2 万字 |
> | 实际过程                       | 13 万字的信，分 4 次打印完  |
>
> **信的长度**和**一次打印多少**是两回事，不矛盾。
>
> 
>
> --enable-auto-tool-choice` 的作用是：**允许模型自己决定什么时候去调用你提供的工具（函数）。**
>
> 简单说，它让 vLLM 把“要不要调用工具”的选择权交给模型。你需要配合另一个参数 `--tool-call-parser` 一起使用，才能让功能生效。
>
> 🛠️ 为什么需要它
>
> 在 OpenAI 的接口标准里，有一个 `tool_choice` 参数，用来控制模型如何调用工具：
>
> *   **`tool_choice="none"`**：模型禁止调用工具，只能输出文本。
> *   **`tool_choice="auto"`**：模型**自己判断**是否需要调用工具。如果它觉得用户的提问需要借助某个工具（比如查询天气、搜索网页），它就会返回一个结构化的“工具调用请求”，而不是直接回答。
> *   **`tool_choice={"type": "function", "function": {"name": "xxx"}}`**：强制模型必须调用指定的那个工具。
>
> `--enable-auto-tool-choice` 就是用来开启 **`"auto"`（自动选择）** 这个模式的。如果不加这个参数，当你请求里设置 `tool_choice="auto"` 时，vLLM 服务器会直接报错，因为它默认不支持这种自动选择行为。
>
> 🔗 必须成对使用：`--tool-call-parser`
>
> 光开启 `--enable-auto-tool-choice` 还不够。模型输出的工具调用请求，通常是一段**特定格式的文本**（比如一段 JSON，或者某种特殊标记包裹的内容）。vLLM 需要一个“解析器”来读懂这段文本，并把它转换成 OpenAI 标准的结构化数据返回给你。
>
> 这就是 `--tool-call-parser` 的作用。
>
> 你之前的启动命令里，这两个参数是配套出现的：
> ```bash
> --enable-auto-tool-choice \
> --tool-call-parser qwen3_xml
> ```
> 这表示：允许模型自动决定调用工具，并且使用 `qwen3_xml` 这个解析器来解读 Qwen3 模型输出的工具调用格式。
>
> 💡 对你的裁判任务有影响吗？
>
> **通常没有直接影响。** 你的 Qwen3-30B 是作为裁判模型，对轨迹进行打分，一般不需要它去调用外部工具（比如查数据库、发邮件）。
>
> 这两个参数在你之前的启动命令里，可能是为了兼容某些需要工具调用的实验场景而保留的。如果你确定裁判任务不会用到 `tools` 参数，那么**去掉这两个参数通常也不会影响你的打分流程**。不过，保留它们也没什么坏处，只要 `qwen3_xml` 解析器能正常工作即可。



请求端在autodl时配置环境变量：

```
export AWM_SYN_LLM_PROVIDER=openai
export OPENAI_BASE_URL=https://uu808973-a936-a773f4d9.westd.seetacloud.com:8443/v1
export OPENAI_API_KEY=EMPTY              # vLLM 不校验 key，给个非空值即可
export AWM_SYN_OVERRIDE_MODEL=Qwen3-30B-A3B-Instruct-2507-FP8
```

请求端在powershell里时配置：

```
$env:AWM_SYN_LLM_PROVIDER="openai"
$env:OPENAI_BASE_URL="https://uu808973-a936-a773f4d9.westd.seetacloud.com:8443/v1"
$env:OPENAI_API_KEY="EMPTY"              # vLLM 不校验 key，给个非空值即可
$env:AWM_SYN_OVERRIDE_MODEL="Qwen3-30B-A3B-Instruct-2507-FP8"
```

注意OPENAI_BASE_URL是在autodl控制面板的“自定义服务”里取6008或6006对应的那个地址（看你使用6008还是6006启动的服务端），再拼上一个v1，而不是从登录指令里自己去拼接

### 下载并启动Qwen2.5-Coder-32B-Instruct

下载：

```
modelscope download --model Qwen/Qwen2.5-Coder-32B-Instruct \
  --local_dir /root/autodl-tmp/ms-cache/models/Qwen2.5-Coder-32B-Instruct
```

这是一个 32B 的 Dense 模型，BF16 精度下权重约 **64GB**。你的 96GB 显存可以**全精度直接运行**，不需要量化，保留最强的代码能力。

```
export VLLM_ALLOW_LONG_MAX_MODEL_LEN=1

vllm serve /root/autodl-tmp/ms-cache/models/Qwen2.5-Coder-32B-Instruct \
  --served-model-name Qwen2.5-Coder-32B-Instruct \
  --host 0.0.0.0 \
  --port 6008 \
  --tensor-parallel-size 1 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --max-num-seqs 4 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --hf-overrides '{"rope_parameters": {"rope_type": "yarn", "factor": 4.0, "original_max_position_embeddings": 32768}}'
```

```
export AWM_SYN_LLM_PROVIDER="openai"
export OPENAI_BASE_URL="http://127.0.0.1:6008/v1"
export OPENAI_API_KEY="EMPTY"
export AWM_SYN_OVERRIDE_MODEL="Qwen2.5-Coder-32B-Instruct"
```



export AWM_SYN_LLM_PROVIDER="openai"
export OPENAI_BASE_URL="http://127.0.0.1:6008/v1"
export OPENAI_API_KEY="EMPTY"
export AWM_SYN_OVERRIDE_MODEL="Qwen3-Coder-30B-A3B-Instruct-FP8"





```
$env:AWM_SYN_LLM_PROVIDER="openai"
$env:OPENAI_BASE_URL="https://uu808973-bcry-3fda2eec.westd.seetacloud.com:8443/v1"
$env:OPENAI_API_KEY="EMPTY"              # vLLM 不校验 key，给个非空值即可
$env:AWM_SYN_OVERRIDE_MODEL="Qwen3-Coder-30B-A3B-Instruct-FP8"
```





### 下载并启动Qwen3-Coder-30B-A3B-Instruct

```
modelscope download --model Qwen/Qwen3-Coder-30B-A3B-Instruct \
  --local_dir /root/autodl-tmp/ms-cache/models/Qwen3-Coder-30B-A3B-Instruct
```

这是一个 30B 的 MoE 模型（激活 3B），BF16 权重约 **60GB**。同样建议用 BF16 精度运行，保留完整能力。

bash

```
vllm serve /root/autodl-tmp/ms-cache/models/Qwen3-Coder-30B-A3B-Instruct \
  --served-model-name Qwen3-Coder-30B-A3B-Instruct \
  --host 0.0.0.0 \
  --port 6008 \
  --tensor-parallel-size 1 \
  --max-model-len 131072 \
  --gpu-memory-utilization 0.90 \
  --max-num-seqs 12 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder
```

```
$env:AWM_SYN_LLM_PROVIDER="openai"
$env:OPENAI_BASE_URL="https://uu808973-y6ap-e4ed3635.westd.seetacloud.com:8443/v1"
$env:OPENAI_API_KEY="EMPTY"              # vLLM 不校验 key，给个非空值即可
$env:AWM_SYN_OVERRIDE_MODEL="Qwen3-Coder-30B-A3B-Instruct"
```

```
export AWM_SYN_LLM_PROVIDER="openai"
export OPENAI_BASE_URL="http://127.0.0.1:6008/v1"
export OPENAI_API_KEY="EMPTY"            
export AWM_SYN_OVERRIDE_MODEL="Qwen3-30B-A3B-Instruct-2507-FP8"
```



## Citation

若使用本项目，请引用原论文：

```bibtex
@article{wang2026agentworldmodelinfinity,
      title={Agent World Model: Infinity Synthetic Environments for Agentic Reinforcement Learning},
      author={Zhaoyang Wang and Canwen Xu and Boyi Liu and Yite Wang and Siwei Han and Zhewei Yao and Huaxiu Yao and Yuxiong He},
      year={2026},
      eprint={2602.10090},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2602.10090},
}
```

