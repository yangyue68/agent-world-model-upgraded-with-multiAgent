# 第四阶段：示例数据生成（sample）改造说明

> 本文件只针对流水线**第四阶段（示例数据生成）**，说明改造前后各自的代码流程，以及改造后的优点与创新点。
>
> 对应文件：
> - 改造前（原版）：`awm/core/sample.py`
> - 改造后：`ma_upgrade/stages/sample/generate.py`（主流程）、`prompts.py`（多 agent 提示词）、`validators.py`（确定性规则校验）
> - 复用原版：`create_sqlite_database`（建库/重置数据库函数）

---

## 一、定位

第四阶段的职责：为第三阶段建好的每个场景的数据库表结构，生成**「初始状态」的示例数据**（一组 INSERT 语句），并**真正插入 .db 文件**，作为后续阶段（API 规范 → 环境仿真 → RL）的数据底座。

示例数据有个关键约束：它是「任务执行前」的快照——任务要 **CREATE/ADD** 的对象不能预填（那是 agent 运行时要创建的），但任务要 **READ/UPDATE/DELETE** 的对象、以及外键依赖的父行必须预填好。

改造版对原版做了「**从「失败率容忍」到「100% 零错误」**」的重构：原版允许 10% 的 INSERT 失败，改造版要求**每条 INSERT 都执行成功**，并通过「一次性生成 + 外键依赖排序 + 两层验证 + Fixer 修复」把失败率压到最低。

---

## 二、改造前流程（原版 `awm/core/sample.py`）

```
读任务清单 + 表结构
  ↓ 单一 prompt（SAMPLE_DATA_GENERATION_SYSTEM_PROMPT + USER_PROMPT）一次生成所有表的 INSERT
  ↓ 重置数据库（清空数据、保留表结构）
  ↓ 执行 INSERT，计算【失败率】= 失败条数 / 总条数
  ↓ 失败率 ≤ error_threshold（默认 0.1）→ 算成功（允许少量失败）
  ↓ 失败率超阈值 → summarize_errors（SQL 专家）把报错总结成「避错指南」喂回重试
  ↓ 重试耗尽 → 挑历史结果里「失败率最低」的那次兜底
```

**原版的特点**：
- 质量控制：**只靠「失败率阈值」**（允许 10% 的 INSERT 失败），没有规则校验、没有语义评审。
- 错误修复：靠 `summarize_errors` 把报错总结成避错指南重试，比较粗糙。
- 失败兜底：重试耗尽仍失败，挑「失败率最低」的结果写产物（可能仍带坏数据）。
- 无外键依赖排序（按生成顺序执行，容易外键失败）、无断点续跑、无进度条。

---

## 三、改造后流程（`ma_upgrade/stages/sample/generate.py`）

```
读任务 + 表结构
  ↓ 场景级并发（线程池 + 进度条），每线程独立构建 agents + GPTClient
  ↓ 断点续跑：跳过 output 里已完成的场景

① 一次性生成全库（data_generator，温度 0.7）
  └─ 一个 prompt 生成整个场景所有表的 INSERT（强调外键引用父表真实主键）

② 规则校验（validate_sample，确定性、非 LLM）
  └─ 表在 schema 中 / 有 INSERT / 每条是 INSERT 且以分号结尾 / 无认证字段

③ 语义评审（data_reviewer，温度 0.0）
  └─ 逐表 verdicts：列名/类型/外键/真实度/是否覆盖任务/是否误预填了任务要创建的对象

④ 真正执行（清库 + 按外键拓扑排序逐条执行 INSERT）
  └─ PRAGMA foreign_keys=ON，父表先插、子表后插

⑤ 失败 → fixer 定向修复（只修失败表，错误累积）→ 再执行
  └─ 修复后仍失败 → 带累积错误反馈，重新一次性生成全库

failed == 0（100% 零错误）才保留；否则重试 max_retry 次，仍失败则放弃该场景（清空 .db 数据、不写产物）
```

---

## 四、改造后的优点与创新点

### 0. 多 agent 设计：三个专职角色（职责分离 + 温度分离）

改造版把原版的「单一 prompt 一路复用」拆成**三个专职 agent**，各司其职、各配不同温度：

| 角色 | agent | 温度 | 职责 | 在流程中的位置 |
|---|---|---|---|---|
| 生成者 | data_generator | 0.7 | 一次性生成整个场景所有表的 INSERT | ① 生成 |
| 评审者 | data_reviewer | 0.0 | 逐表评审数据质量（列名/类型/外键/覆盖任务） | ③ 语义评审 |
| 修复者 | fixer | 0.0 | 定向修复执行失败的 INSERT（只修失败表） | ⑤ 修复 |

**设计要点**：

1. **职责分离**：生成 / 评审 / 修复由三个不同 agent 承担，避免一个 agent 既生成又评审又修复。
2. **温度分离**：生成偏多样（0.7），评审和修复要稳定（0.0）。
3. **分工流水线**：data_generator 生成 → data_reviewer 评审 → 真正执行 → 报错则 fixer 修复。

### 1. 一次性生成全库（表与表一起生成，保证外键一致）

原版虽然是「一次生成所有表」，但改造版的关键改进是：**prompt 里第 6 条明确要求「外键必须引用你在父表里真实插入的主键 id」**（例如父表插了 id 1,2,3，子表就只能引用 1/2/3，不能引用 10）。这让 LLM 在生成时就自洽地维护了跨表外键，从源头减少外键错误。

### 2. 外键依赖排序（父表 → 子表）

新增 `_topo_sort_table_names`：解析每张表 DDL 里的 `REFERENCES` 提取外键关系，做拓扑排序；`execute_inserts` 执行前先把表按「父表在前、子表在后」重排。即使生成的表顺序不对，也不会因为「子表先插、父表还没数据」而外键失败。

### 3. 「两层验证」：规则层 + 语义层（原版完全没有）

| 层 | 谁来判 | 判什么 | 成本 |
|---|---|---|---|
| 规则校验 | 代码（validators.py） | 表在 schema 中、有 INSERT、每条是 INSERT 且以分号结尾、无认证字段 | 免费、确定性 |
| 语义评审 | LLM（data_reviewer） | 列名/类型/外键/真实度/是否覆盖任务/是否误预填任务要创建的对象 | LLM 调用 |

### 4. Fixer 定向修复 + 错误累积

原版失败后靠 `summarize_errors` 把报错总结成避错指南重试。改造版用 **fixer 定向修复**（只修失败表），且**错误累积传递**（`accumulated_errors |= errors`）：每轮执行的新错误都累积起来，传给下一次修复和重生成，不丢失历史错误。

### 5. 100% 零错误标准（放弃兜底，不写坏数据）

原版允许失败率 ≤ 10%，产物里可能带坏数据。改造版要求 `failed == 0`（每条 INSERT 都成功），重试耗尽直接**放弃该场景**（清空 .db 数据、不写 `gen_sample_ma.jsonl`），保证产物里的每个场景数据都完整可执行。

### 6. 断点续跑 + 进度条 + 失败原因日志

- 支持 `--resume`（默认开启）：跳过 output 里已完成的场景，结束时合并去重；output 无产物时自动全新生成。
- `run()` 用 `tqdm` 显示进度条（`生成示例数据`）。
- 每条 INSERT 失败实时 `logger.error` 打印具体报错；放弃场景时打印「错误示例」。

---

## 五、运行方式

所有命令都在**项目根目录 `ma_upgrade/`** 下执行，入口是 `python -m stages.sample.generate`。

### 方式一：全新生成（output 无产物时自动触发）

```bash
python -m stages.sample.generate \
  --input_task ma_upgrade/stages/task/output/gen_tasks_ma.jsonl \
  --input_db ma_upgrade/stages/db/output/gen_db_ma.jsonl
```

执行流程：

1. 读取任务文件（`gen_tasks_ma.jsonl`）+ 表结构文件（`gen_db_ma.jsonl`）。
2. 只处理「有对应表结构」的场景。
3. 并发（进度条）为每个场景：一次性生成全库 → 规则校验 → 语义评审 → 按外键顺序执行 → fixer 修复。
4. `failed == 0` 的场景写入 `output`（默认 `ma_upgrade/stages/sample/output/gen_sample_ma.jsonl`），并真正把数据插入 `.db` 文件（`database_dir`）。
5. 失败的场景放弃（清空 .db 数据、不写产物）。

### 方式二：断点续跑（默认）

`--resume` 默认开启：`output` 已有产物时自动跳过已完成场景；无产物时自动全新生成。

```bash
python -m stages.sample.generate \
  --input_task ma_upgrade/stages/task/output/gen_tasks_ma.jsonl \
  --input_db ma_upgrade/stages/db/output/gen_db_ma.jsonl
```

`--resume` 默认开启：加载 `output` 里已完成的场景名并跳过，只处理剩下的，结束时把新旧结果合并（去重）保存；`output` 无产物时自动全新生成。

### 关键参数

| 参数 | 含义 |
|---|---|
| `--input_task` | 任务文件（第二阶段的产物 `gen_tasks_ma.jsonl`） |
| `--input_db` | 表结构文件（第三阶段的产物 `gen_db_ma.jsonl`） |
| `--output` | 输出文件（默认 `ma_upgrade/stages/sample/output/gen_sample_ma.jsonl`） |
| `--database_dir` | .db 文件存放目录（默认 `ma_upgrade/stages/db/output/databases`） |
| `--model` | 模型名（或用环境变量 `AWM_SYN_OVERRIDE_MODEL`） |
| `--max_retry` | 生成+评审+修复循环最大轮次（默认 2） |
| `--max_concurrency` | 场景级并发数（默认 8） |
| `--resume` | 断点续跑（默认开启，output 无产物时自动重新生成） |
| `--allowed_scenarios` | 只处理指定场景 |
| `--limit` | 只处理前 limit 个场景 |

### ⚠️ 注意事项

1. **模型名**：`--model` 默认是占位符，必须用 `--model` 或环境变量 `AWM_SYN_OVERRIDE_MODEL` 覆盖，否则 `pre_process` 会 assert 失败。

2. **成功标准是 100% 零错误**：一个场景里只要有一条 INSERT 执行失败（外键/列名/类型/约束），该场景就不算成功；重试 `max_retry` 次后仍失败就**放弃**（清空 .db 数据、不写产物），而不是写带坏数据的兜底。

3. **产物有两份**：`--output` 保存示例数据（jsonl，每行 `{scenario, tables_count, inserts_count, sample_data}`）；`--database_dir` 里的 `.db` 文件会被真正插入数据（成功场景）。

4. **外键约束开启**：执行时 `PRAGMA foreign_keys = ON`，所以外键引用错误会直接导致 INSERT 失败——这正是「一次性生成 + 外键排序 + 外键 prompt 约束」三道保险要解决的问题。

---

## 六、一句话总结

改造版第四阶段 = **原版「单一 prompt + 失败率阈值」→ 「一次性生成全库 + 外键依赖排序 + 规则校验 + 语义评审 + Fixer 修复（错误累积）」**。核心创新：① 一次性生成 + 外键 prompt 约束 + 拓扑排序，从源头和顺序上消除外键错误；② 用「规则层 + 语义层」两层验证保证数据质量；③ 要求 `failed == 0`（100% 零错误），失败即放弃、不写坏数据；④ 断点续跑 + 进度条 + 失败原因日志，让生成过程可观测、可续跑。
