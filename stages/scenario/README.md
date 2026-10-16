# 第一阶段：场景生成（scenario）改造说明

> 本文件只针对流水线**第一阶段（场景生成）**，说明改造前后各自的代码流程，以及改造后的优点与创新点。
>
> 对应文件：
> - 改造前（原版）：`agent-world-model/awm/core/scenario.py`
> - 改造后：`ma_upgrade/stages/scenario/generate.py`（主流程）、`validators.py`（确定性规则校验）、`prompts.py`（评审提示词）

---

## 一、定位

第一阶段的职责：从一批「种子场景」（真实网站/App，如 Amazon、GitHub）出发，用 Self-Instruct 的方式让 LLM **批量生成大量新的、多样的、适合做 API 仿真的场景**，作为后续阶段（任务生成 → 数据库 → 环境 → RL）的输入。

改造版对原版做了「**轻量改造**」：**保留原版的核心骨架**（Self-Instruct 主循环、few-shot、类别上限、stall 放宽、embedding 去重），**补齐两块关键能力**（对齐 ma_upgrade 的「两层验证」设计）：

1. 确定性规则校验（`validators.py`）——能程序化验证的绝不让 LLM 评。
2. 评审重生成闭环（reviewer）——不合格的场景带意见重生成，而不是「丢弃式过滤」。

---

## 二、改造前流程（原版 `awm/core/scenario.py`）

```
load_existing
  └─ 加载种子场景（seed_scenario.jsonl）

Step 1: 分类（classify_scenarios）
  └─ 用 LLM 给每个种子打 suitability_score / suitability_level / categories / features
  └─ filter_by_suitability：按 high / medium / low 分桶，few-shot 优先选 high

Step 2: Self-Instruct 主循环（while current_count < target_count）
  ├─ generate_batch：从 high 池 few-shot，LLM 并行生成新场景
  │    └─ _parse_generation_response：只保留 name + description
  ├─ check_diversity：LLM 评审多样性（decision=keep/reject）
  │    └─ reject 的直接【丢弃】
  ├─ add_scenarios：按 name 去重后入库
  └─ save_progress：落盘

（可选）analyze_category_distribution：LLM 分析类别分布，找 underrepresented 类别
```

**原版的特点**：
- 质量把关**几乎全靠 LLM**（分类、多样性检查都是 LLM 调用）。
- 多样性检查是「**丢弃式**」——评审 reject 就丢掉，不重生成，浪费已生成的内容。
- 没有确定性规则校验，也没有统计报告。

---

## 三、改造后流程（`ma_upgrade/stages/scenario/generate.py`）

```
load_existing
  └─ 加载场景（非续跑：从 input 读原始种子；续跑：优先从 output 读上次进度）

Step 1: 种子质量把关（_quality_gate，四道关）
  ├─ ① 规则校验（validate_scenarios，确定性）
  ├─ ② suitability 打分（classifier）——删除 low/medium，只保留 high
  ├─ ③ embedding 去重（check_embedding_diversity，确定性）
  ├─ ④ LLM 评审（reviewer）——不合格【直接丢弃】（种子是真实网站，不重生成）
  │    └─ 注意：种子评审时【不对照已有场景】（include_existing=False），只判可仿真性 + 具体性；去重已由第③关 embedding 完成
  └─ filter_by_suitability：设置 few-shot 池（high）+ 重建去重名单

initialize_embeddings
  └─ 用 embedding 模型把已有场景编码成向量矩阵（后续去重用）

Step 2: Self-Instruct 主循环（while current_count < target_count）
  ├─ _generate_batch：generator agent（温度 1.0，多样）few-shot 生成（只保留 name + description）
  ├─ 攒批后，新场景也过四道关（_quality_gate）
  │    ├─ ① 规则校验（名称合法 / 描述长度 / 无 GUI 关键词）
  │    ├─ ② suitability 打分（classifier）——删除 low/medium
  │    ├─ ③ embedding 去重（确定性相似度，与全局池累积比对，非仅批内）
  │    └─ ④ LLM 评审——对照已有场景，判可仿真性 + 多样性 + 具体性；不合格带 issue 重生成；重生成的重新过前 3 道关再评审；仍不合格 / 评审失败 → 丢弃
  ├─ add_scenarios（只合格入库）+ add_to_embedding_pool
  └─ save_progress + stats 统计报告
```

**改造后的关键变化**：① 种子和新场景**统一过四道关**（规则校验 → suitability 打分 → embedding 去重 → LLM 评审）；② 能程序化验证的交给代码、只能语义判断的交给 LLM；③ 评审不合格：新场景带 issue 重生成、种子直接丢弃，仍不合格或评审失败一律丢弃——保证「**凡进产物者皆合格**」。

---

## 四、改造后的优点与创新点

### 1. 「四道把关」：确定性层 + LLM 层分工（种子、新场景统一过）

| 关 | 谁来判 | 判什么 | 成本 |
|---|---|---|---|
| ① 规则校验 | 代码（validators.py） | 名称合法、描述长度、GUI 关键词、精确去重、类别归一化 | 免费、毫秒级、确定性 |
| ② suitability 打分 | LLM（classifier） | 可仿真性（是否适合 API 仿真），删 low/medium，补 categories/features | LLM 调用 |
| ③ embedding 去重 | 代码（相似度） | 场景是否语义重复 + 类别上限 | 免费、确定性 |
| ④ 语义评审 | LLM（reviewer） | 可仿真性 + 具体性（种子，不判多样性）+ 多样性（新场景，对照已有场景），reject 带 issue | LLM 调用 |

**核心原则**：能程序化验证的绝不让 LLM 评（省钱、稳定、可复现）。种子场景和新生成的场景**都统一过这四道关**。原版把「名称、长度、GUI」这类本可程序化判断的东西也交给 LLM 评审，又贵又不稳定。

### 2. 确定性规则校验（`validators.py`）

新增了原版完全没有的确定性规则：

- `rule_name_valid`：场景名非空、长度 2~80。
- `rule_desc_length`：描述长度 50~3000 字符。
- `rule_no_gui`：剔除描述含 GUI/下载/点击/上传文件等关键词的场景（这类不适合 API 仿真）。
- `dedup_scenarios`：按 name 精确去重。
- `normalize_categories`：类别别名归一化（`ecommerce` / `e-commerce` / `e_commerce` → `E-commerce/Marketplace`），避免类别统计被别名打散。

### 3. embedding 去重替代「LLM 评重复」

原版用 `check_diversity`（LLM 评审）判断「新场景是否与已有重复」，慢且贵。改造版用 **embedding 余弦相似度**（`check_embedding_diversity`）做确定性去重——快、免费、可复现，LLM 只保留给「真正的语义评审」。

### 4. 评审重生成闭环（不再「丢弃式过滤」）

这是最核心的创新点：

- 原版：评审 reject → **直接丢弃**，已生成的内容浪费掉。
- 改造版：reviewer 输出 `{pass: false, issue: "具体可执行的问题"}` → **generator 带着 issue 重新生成**。
- **区分处理**：新场景评审被拒 → 带 issue 重生成（重生成的重新过前 3 道关再评审）；种子评审被拒 → 直接丢弃（种子是真实网站，不该被 LLM 替代品替换）。
- **种子评审不对照已有场景**：`_review` 有 `include_existing` 参数，种子评审时传 `False`（种子是最早的场景，无「已有场景」可对照，去重已由 embedding 完成），reviewer 只判可仿真性 + 具体性；新场景评审时传 `True`（默认），对照已有场景再判多样性。
- 重生成后仍不合格 → 丢弃；评审失败（LLM 返回异常）→ **也丢弃**（而非"全部保留"），确保「未确认合格的场景」绝不进入最终产物。

好处：不合格的场景不再白生成，而是作为「带反馈」的输入进入下一轮，**提高合格率、减少浪费、形成自我改进闭环**。

### 5. 多 agent 设计：三个专职角色（职责分离 + 温度分离）

改造版把原版的「单一 client + 单一 prompt 一路复用」拆成**三个专职 agent**，各司其职、各配不同温度：

| 角色 | agent | 温度 | 职责 | 在四道关中的位置 |
|---|---|---|---|---|
| 生成者 | generator | 1.0 | 生成新场景（few-shot + 类别引导），只产出 name + description | 主循环 `_generate_batch` + 重生成 `_regenerate` |
| 打分者 | classifier | 0.0 | 给场景打 suitability 分 + 分类（categories / simulatable_features），删 low/medium | ② suitability 打分 |
| 评审者 | reviewer | 0.0 | 评审可仿真性 + 具体性（新场景还判多样性），reject 带 issue | ④ LLM 评审 |

**设计要点**：

1. **职责分离**：生成 / 打分 / 评审由三个不同 agent 承担，避免「既当运动员又当裁判」——generator 只负责产出，classifier 只负责打分建档，reviewer 只负责把关淘汰。
2. **温度分离**：生成类（需要多样性）用高温 1.0；评判类（打分 / 评审，需要稳定）用低温 0.0。
3. **分工流水线**：generator 产出 → classifier 打分（删 low/medium）→ reviewer 评审（reject 带 issue 供重生成）。每个 agent 只做一件事，职责清晰、可单独调 prompt。

### 6. 统计报告（`stats` + `*.report.json`）

新增 `stats` 字典，记录 `generated / rule_rejected / review_rejected / regenerated / classification_rejected` 等，运行结束后输出一份 `.report.json`，**可量化每个环节的损耗**，便于调参和排查（原版没有）。

### 7. 断点续跑（`resume`）+ 定期保存

`save_progress` 每轮落盘，支持 `--resume` 断点续跑：续跑时优先从 output_path 加载上次进度（high 种子 + 已生成场景），跳过分类，从上次的 current_count 继续生成到 target（原版也有 save，但改造版补齐了 resume 的加载逻辑）。详见下方「运行方式」。

### 8. 批量并发请求（`batch_chat_completion`）

第一阶段主循环是单线程的，generator / classifier / reviewer 都用 `batch_chat_completion` 一次性批量发送请求（进度条 + 并发），而不是逐条串行调用，从而在单进程内就获得并发收益。（「每线程独立 GPTClient」是第二阶段 task 等用了线程池的阶段的并发安全做法，第一阶段不涉及。）

---

## 五、运行方式

所有命令都在**项目根目录 `ma_upgrade/`** 下执行，入口是 `python -m stages.scenario.generate`。

### 环境准备：

配置生产LLM，除RL之外的所有阶段都要使用，推荐使用deepseek-v4-pro，因为后续若干阶段要求LLM一次生成大量内容。作者踩过的一个坑是使用了qwen-plus，但是qwen-plus最大输出长度是32768个tokens，也就是不到10万个字符，这对于一次性输出大量代码来说非常危险，代码会被截断，造成产物失败率极高。虽然各个阶段都进行了优化（例如第六阶段从要求LLM一次吐出上千行代码改成了先输出文件清单，歇口气再输出具体文件），对LLM输出能力要求降低，但是依然推荐输出能力更强的模型。deepseek-flash输出长达384k个tokens，完全足够。

在powershell上使用举例：

```
$env:AWM_SYN_LLM_PROVIDER = "openai"
$env:OPENAI_API_KEY = "sk-f42845a5a6c34f84a191bb59870154ab"
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:AWM_SYN_OVERRIDE_MODEL = "deepseek-flash"
```

在autoDL等服务器上使用举例：

```
export AWM_SYN_LLM_PROVIDER="openai"
export OPENAI_API_KEY="sk-ant-oat01-WmE5vL5Q41ZpvOkP4PhvP8a2C9j5_C1XDg9DZZgNt3hrc7_QJrGAIcw1ITSuztemtTn5zT5UQJIljCY2Vbf1VoWeloOF6AA"
export OPENAI_BASE_URL="https://code.newcli.com/codex/v1"
export AWM_SYN_OVERRIDE_MODEL="gpt-5.5"
```

配置embedding模型（只有第一阶段场景生成需要使用）：

在powershell上使用举例：

```
$env:EMBEDDING_OPENAI_API_KEY  = "sk-ws-H.EERLPXH.QBz4.MEUCIQDAKS4k7LsvVxGm1MnSFRikfSv8r20040AEvjFBNif5WQIgecYPrDw7D9Akib0qS6mJCJ60pTK1L_oaH9Tvatd2uiA"   
$env:EMBEDDING_OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:EMBEDDING_OPENAI_MODEL    = "text-embedding-v4"    
```

在autoDL等服务器上使用举例：

```
export EMBEDDING_OPENAI_API_KEY="sk-ws-H.PRYMMII.y7R6.MEQCID-07ynLeq7SiTLbA7lT4qgLkCHpV0OweiNOttDg2xw2AiAmFhafIu0piyjlcQpY2mM5-aAiMlP6YVg-aloK-HcFng"
export EMBEDDING_OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
```



### 方式一：全新生成（output 无产物时自动触发）

从「原始种子文件」开始完整跑一遍：种子过四道关 → 生成新场景（也过四道关）→ 凑够 target。

```bash
python -m stages.scenario.generate \
  --input_path stages/scenario/seed_scenario.jsonl \
  --target_count 1000 \
  --resume False
```

注意powershell不能识别反斜杠，可以把“\”改成“`”或者直接写成一行：

```
python -m stages.scenario.generate --input_path stages/scenario/seed_scenario.jsonl --target_count 1000 --resume False
```



执行流程：

1. `load_existing` 从 `input_path`（原始种子）加载，此时种子**未分类、可能含 low/medium**（这是唯一允许「读到不合格」的地方）。
2. 种子过 `_quality_gate` **四道关**：规则校验 → suitability 打分（删 low/medium）→ embedding 去重 → LLM 评审（不合格直接丢弃，不重生成）。
3. `filter_by_suitability` 设置 few-shot 池（high）+ 重建去重名单。
4. **把筛选后的合格种子保存回 `input_path`**（供下次 resume 复用）。
5. 主循环生成新场景，每批新场景也过 `_quality_gate` 四道关，凑够 `target_count`（= 合格种子数 + 新生成数）。
6. 产物写入 `output_path`（默认 `ma_upgrade/stages/scenario/output/gen_scenario_ma.jsonl`），**只有合格种子 + 合格新场景**。

### 方式二：断点续跑（默认）

`--resume` 默认开启：`output` 目录已有产物时，自动从 `output_path` 续跑；无产物时自动读种子重新生成。

```bash
python -m stages.scenario.generate \
  --input_path stages/scenario/seed_scenario.jsonl \
  --target_count 150
```

执行流程：

1. `load_existing` **优先从 `output_path` 加载**上次进度（= 之前的合格种子 + 已生成场景）；若 `output_path` 不存在，回退到 `input_path`（上次保存的合格种子）。
2. **跳过质量把关**（output/input 里已经是合格的，无需再过四道关）。
3. `filter_by_suitability` 只设置 few-shot 池 + 去重名单。
4. `initialize_embeddings` 把已加载的场景全部编码。
5. 主循环从上次的 `current_count` 继续，生成到 `target_count`（新生成的仍过四道关）。

⚠️ **续跑要求**：`target_count` 要和首次运行时**一致或更大**（若设小了，主循环条件 `current_count < target` 不满足，会直接结束、生成 0 个）。

### 关键参数

| 参数 | 含义 |
|---|---|
| `--input_path` | 种子场景文件（`stages/scenario/seed_scenario.jsonl`） |
| `--target_count` | **最终总场景数**（= 合格种子数 + 新生成数），不是「新增数」 |
| `--model` | 模型名（或用环境变量 `AWM_SYN_OVERRIDE_MODEL`） |
| `--resume` | 断点续跑（默认开启；output 无产物时自动回退读种子） |
| `--skip_classification` | 跳过种子分类（种子已带分类字段时用） |
| `--max_retry` | 评审重生成循环最大轮次 |
| `--max_total_iterations` | 主循环最大迭代轮数（默认 3，太小，实际要调大） |

### ⚠️ 注意事项

1. **`target_count` 语义**：它是「总场景数」，不是「新增数」。例如合格种子 100 个、想再新增 400 个，要传 `--target_count 500`（不是 400）。

2. **`max_total_iterations` 默认太小**：默认值是 3，主循环最多跑 3 轮（每轮约 50 个候选）。要生成几百个场景，必须把它调大（例如 `--max_total_iterations 100`），否则没凑够 target 就提前退出。

3. **模型名**：`--model` 默认是占位符，必须用 `--model` 或环境变量 `AWM_SYN_OVERRIDE_MODEL` 覆盖，否则 `pre_process` 会 assert 失败。

4. **embedding 依赖**：去重需要 embedding 模型（默认 `text-embedding-v4`），依赖 `EMBEDDING_OPENAI_API_KEY` / `EMBEDDING_OPENAI_BASE_URL` 环境变量。

5. **产物字段**：改造版保存的 `gen_scenario_ma.jsonl` 每行会带 `categories` / `suitability_score` 等分类字段（原版开源产物是剥掉的、只有 name+description）。这些字段是第一阶段的**内部质量控制用**，第二阶段只读 `name` + `description`，所以**不影响下游**，只是文件更大。

6. **产物合格性**：种子和新场景都过「规则校验 → suitability 打分 → embedding 去重 → LLM 评审」四道关，最终产物只含全部通过的场景；low/medium、规则被拒、去重被拒、评审被拒、评审失败的场景都不会进入产物。

7. 如何调整并发度？num_parallel_requests与scenarios_per_request的乘积就是最大的并发度

   ```
   num_parallel_requests: int = 10              # 每轮并行请求数
   scenarios_per_request: int = 5             # 每个请求生成的场景数
   ```

---

注意：如果已有480个场景，想生产到500个，也就是target_count 设为500，则产生的数量>=500，不一定刚好是500，因为一次并发生产num_parallel_requests*scenarios_per_request个场景，也许都是合格的，加上原有的480个，就可能超过500个了

## 六、一句话总结

改造版第一阶段 = **原版 Self-Instruct 骨架 + 「规则校验 + suitability 打分 + embedding 去重 + 评审重生成闭环」四道质量把关，种子和新场景统一过**。核心创新：① 种子和新场景都过四道关，凡进产物者皆合格；② 能程序化判断的交给代码、只能语义判断的交给 LLM；③ 「丢弃式过滤」升级为「带意见重生成」（新场景重生成、种子直接丢弃），重生成仍不合格或评审失败一律丢弃。

