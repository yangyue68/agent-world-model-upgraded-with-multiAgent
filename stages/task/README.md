# 第三阶段：任务生成（task）改造说明

> 本文件只针对流水线**第三阶段（任务生成）**，说明改造前后各自的代码流程，以及改造后的优点与创新点。
>
> 对应文件：
> - 改造前（原版）：`agent-world-model-main/awm/core/task.py`
> - 改造后：`ma_upgrade/stages/task/generate.py`（主流程）、`prompts.py`（多 agent 提示词）
> - 规则校验：`ma_upgrade/stages/task/validators.py`（`rule_no_auth` / `rule_no_gui` / `rule_length` / `validate_tasks_paired`）

---

## 一、定位

第三阶段的职责：读取第一阶段产出的场景（`gen_scenario_ma.jsonl`）+ 第二阶段产出的角度（`gen_angles_ma.jsonl`），为每个场景生成若干条「用户任务」——即真实用户会在这个网站/App 上做的操作（如「在 Amazon 搜索 laptop 并加入购物车」），作为后续阶段（数据库 → 环境 → RL）的输入。

改造版对原版做了「**从数量到质量**」的重构：原版只是「一个 prompt 让 LLM 一次生成 N 条」，改造版引入「**角度级闭环 + 两层验证**」，保证任务**多样、合格、可程序化验证**。

---

## 二、改造前流程（原版 `awm/core/task.py`）

```
读场景（gen_scenario.jsonl）
  ↓ 单一 prompt（TASK_GENERATION_SYSTEM_PROMPT + USER_PROMPT）
  ↓ LLM 一次生成 num_tasks 条任务（JSON 数组）
  ↓ json 解析 + 校验任务【数量】（不够就重试）
  ↓ 保存（只取前 num_tasks）
```

**原版的特点**：
- 生成方式：一条 prompt 自由生成，**没有角度规划**（可能生成一堆同类任务）。
- 质量控制：**只校验数量**（够不够 num_tasks 条），不校验质量、不校验「是否涉及认证/GUI/下载」。
- 无去重、无语义评审。
- 重试：数量不足就原样重试（不带反馈）。

---

## 三、改造后流程（`ma_upgrade/stages/task/generate.py`）

```
读场景（gen_scenario_ma.jsonl）+ 角度（gen_angles_ma.jsonl）
  ↓ 场景级并发（线程池；每个场景内再按角度并发）

① 每个 angle 独立走完整闭环（角度级并发，并发度 = 该场景的 angle 数量）：
    generator 从零生成该 angle 的 tasks（tasks_per_angle 条）
    → 规则校验（validate_tasks_paired，确定性）
    → 语义评审（reviewer，逐条 verdicts，带 angle）
    → 有规则/语义问题 → fixer 带「完整任务 + 带 angle 的问题清单」定向修复
    → 全通过 → 该 angle 产出全部合格任务

② 合并所有 angle 的合格任务（不截断，有多少保留多少）

若一个合格任务都没有 → 放弃该场景（不写产物）
```

关键点：**每个 angle 的「生成 → 规则校验 → 语义评审 → 修复」是完整闭环**，多个 angle 之间并行、天然隔离；angle 信息从生成到修复全程保留，fixer/reviewer 拿到的 problems 都精确到「哪个角度、哪条任务、什么原因」。

---

## 四、改造后的优点与创新点

### 0. 多 agent 设计：三个专职角色（职责分离 + 温度分离）

改造版把原版的「单一 prompt 一次生成任务」拆成**三个专职 agent**（角度规划/角度评审已拆分到 angle 阶段），各司其职、各配不同温度：

| 角色 | agent | 温度 | 职责 | 在流程中的位置 |
|---|---|---|---|---|
| 生成者 | generator | 1.0 | 从零生成某个角度的任务 | ① 生成 |
| 评审者 | reviewer | 0.0 | 评审任务质量（相关 / 具体 / 可执行 / 无认证 / 无 GUI），逐条 verdicts（带 angle） | ② 语义评审 |
| 修复者 | fixer | 0.0 | 带「完整任务 + 带 angle 的问题清单」定向修复规则/语义问题 | ③ 修复 |

**设计要点**：

1. **职责分离**：生成（generator）/ 评审（reviewer）/ 修复（fixer）三件事由三个 agent 承担。
2. **温度分离**：生成偏多样（1.0），评审和修复要稳定（0.0）。
3. **角度级闭环**：每个 angle 内部独立走「生成 → 规则校验 → 语义评审 → 修复」闭环，多个 angle 并行、天然隔离。

### 1. 角度级并发 + 角度内闭环（天然隔离）

每个 angle 独立走完整闭环（`ThreadPoolExecutor` 并发，并发度 = 该场景 angle 数量），「生成 → 规则校验 → 语义评审 → 修复」都在 angle 内部完成。多个 angle 并行、互不干扰，一个 angle 修不好不影响其它 angle；angle 信息从生成到修复全程保留，fixer/reviewer 拿到的 problems 都精确到「哪个角度、哪条任务、什么原因」。

### 2. 「两层验证」：规则层 + 语义层

| 层 | 谁来判 | 判什么 | 成本 |
|---|---|---|---|
| 规则校验 | 代码（validators.py） | 无认证/登录/注册、无 GUI/下载/直接交互、长度合理 | 免费、确定性 |
| 语义评审 | LLM（reviewer） | 是否与场景相关、是否具体、是否可执行 | LLM 调用 |

**核心原则**：能程序化验证的（认证/GUI/长度）绝不让 LLM 评。原版完全没有这些确定性规则。

### 3. fixer 定向修复（闭环）

规则校验或语义评审发现问题时，把「完整任务 + 带 angle 的问题清单」交给 fixer 定向修复（而不是让 generator 整段重生成）。fixer 保持好任务不变，只修复有问题的任务。

### 4. 每 angle 独立构建 agents（并发安全）

每个 angle 内部独立构建 agents 和 GPTClient（避免跨线程共享 httpx 连接池导致挂起）。

### 5. 两级并发（场景级 + 角度级）

- 场景级：`ThreadPoolExecutor` 并发处理多个场景（`max_concurrency`）；
- 角度级：每个场景内再并发处理多个 angle（并发度 = 该场景 angle 数量）。

### 6. 产物不截断、任务数量不强制一致

合并所有 angle 的合格任务后**不截断、全部保留**（后续阶段消费 tasks 不要求每个场景数量一致）；只有「一个合格任务都没有」才放弃该场景。

---

## 五、运行方式

在**项目根目录 `ma_upgrade/`** 下执行：

```bash
python -m stages.task.generate \
--input_scenario stages/scenario/output/gen_scenario_ma.jsonl \
--input_angles stages/angle/output/gen_angles_ma.jsonl \
--output stages/task/output/gen_tasks_ma.jsonl
```

### 关键参数

| 参数 | 含义 |
|---|---|
| `--input_scenario` | 场景文件（第一阶段的产物 gen_scenario_ma.jsonl） |
| `--input_angles` | 角度文件（第二阶段的产物 gen_angles_ma.jsonl） |
| `--output` | 输出文件（默认 `stages/task/output/gen_tasks_ma.jsonl`） |
| `--tasks_per_angle` | 每个角度生成的任务数（默认 3） |
| `--limit` | 只处理前 limit 个场景（默认不限制） |
| `--shuffle` | 是否打乱场景顺序 |
| `--model` | 模型名（或用环境变量 `AWM_SYN_OVERRIDE_MODEL`） |
| `--max_retry` | 生成 + 修复循环最大轮次（默认 1） |
| `--max_concurrency` | 场景级并发数（默认 8） |
| `--resume` | 断点续跑（默认开启，output 无产物时自动重新生成） |

### ⚠️ 注意事项

1. **模型名**：`--model` 默认是占位符，必须用 `--model` 或环境变量 `AWM_SYN_OVERRIDE_MODEL` 覆盖，否则 `pre_process` 会 assert 失败。

2. **只有「一个合格任务都没有」才放弃场景**：每个 angle 的合格任务会全部保留（不截断、不要求每个场景数量一致）；只有合并后一个合格任务都没有时，才放弃该场景（返回 None，不写产物）。

3. **每角度生成数**：`tasks_per_angle`（默认 3）控制每个角度生成几条任务；角度数量由上游 angle 阶段决定（默认 5 个角度）。

4. **产物字段**：`gen_tasks_ma.jsonl` 每行 `{scenario, tasks, angles}`；输入的角度文件 `gen_angles_ma.jsonl` 每行 `{scenario, angles}`。

---

## 六、一句话总结

改造版第三阶段 = **原版「单一 prompt 自由生成」→ 「角度级并发 + 角度内闭环（生成 → 规则校验 → 语义评审 → fixer 定向修复）」**。核心创新：① 每个 angle 独立走完整闭环、多个 angle 并行天然隔离，angle 信息全程保留；② 用「确定性规则层（认证/GUI/长度）+ LLM 语义层」两层验证保证质量；③ 规则/语义问题统一交给 fixer 带「完整任务 + 带 angle 的问题清单」定向修复（替代整段重生成）；④ 合格任务全部保留、不截断。
