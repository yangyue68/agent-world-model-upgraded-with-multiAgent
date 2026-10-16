# 第三阶段：数据库表结构生成（db）改造说明

> 本文件只针对流水线**第三阶段（数据库表结构生成）**，说明改造前后各自的代码流程，以及改造后的优点与创新点。
>
> 对应文件：
> - 改造前（原版）：`awm/core/db.py`
> - 改造后：`ma_upgrade/stages/db/generate.py`（主流程）、`prompts.py`（多 agent 提示词）、`validators.py`（确定性规则校验）
> - 复用原版：`create_sqlite_database`（真正执行 DDL 建库的函数）

---

## 一、定位

第三阶段的职责：根据第二阶段产出的「用户任务清单」（`gen_tasks_ma.jsonl`），**按任务级**为每个任务设计一套 **SQLite 数据库表结构（DDL）**，并**真正建出 .db 文件**，落地到对应任务级子目录，作为后续阶段（环境/API 仿真 → RL）的数据底座。

改造版对原版做了「**从「能建库就行」到「质量严格把关」**」的重构：原版靠「建库失败率阈值」粗筛，改造版引入「**规则校验 + 语义评审 + Fixer 定向修复闭环**」，要求**每张表都合格、全部建表成功**。同时把生成粒度从「场景级」下沉到「任务级」——每个任务独立设计一套表结构，任务之间互不约束，表结构更简单、更易一次成功。

---

## 二、改造前流程（原版 `awm/core/db.py`）

```
读任务（gen_tasks.jsonl）
  ↓ 单一 prompt（DATABASE_GENERATION_SYSTEM_PROMPT + USER_PROMPT）
  ↓ LLM 一次设计整库表结构（DDL）
  ↓ create_sqlite_database 真正建库
  ↓ 计算【失败率】= 失败表数 / 总表数
  ↓ 失败率 ≤ error_threshold（默认 0.1）→ 算成功（允许少量表失败）
  ↓ 失败率超阈值 → summarize_errors（SQL 专家）把报错总结成「避错指南」喂回重试
  ↓ 重试耗尽 → 挑历史结果里「失败率最低」的那次兜底
```

**原版的特点**：
- 生成方式：一条 prompt 设计整库，**没有规则校验、没有语义评审**。
- 质量标准：**失败率 ≤ 10% 就算成功**（意味着可以有 10% 的表建失败）。
- 错误修复：靠 `summarize_errors` 把报错总结成避错指南重试，比较粗糙。
- 失败兜底：重试耗尽仍失败，从历史结果里挑失败率最低的写产物（可能仍有坏表）。

---

## 三、改造后流程（`ma_upgrade/stages/db/generate.py`）

```
读任务（gen_tasks_ma.jsonl，每行 {scenario, tasks}）
  ↓ 展开成任务级 item：每个任务 → {scenario, task_id(task_i), task}
  ↓ 任务级并发（线程池；每线程独立构建 agents + GPTClient）

① 表结构设计（schema_designer，温度 0.3）—— 只在「首次」或「上一轮表为空」时从零生成
  └─ 根据场景名 + 单个任务，设计整套 DDL + indexes

② 规则校验（validate_tables，确定性、非 LLM）
  └─ 每表有主键（rule_has_primary_key）+ 无认证字段（rule_no_auth_fields）+ DDL 合法（rule_valid_ddl）
  └─ 有表不过 → 记录问题，交给 fixer 修复（不再整段重设计）

③ SQL 语义评审（schema_reviewer，温度 0.0）
  └─ 整体评审 + 逐表 verdicts：漏表 / 错外键 / 字段不足 / 冗余 / 认证字段
  └─ 有表不过 → 记录问题，交给 fixer 修复（不再整段重设计）

④ 真正建库（复用 create_sqlite_database，落地到任务级子目录）
  └─ 要求【全部表建成功】（failed_count == 0），不允许失败率兜底
  └─ .db 落地到 output/<场景名>/task_<i>/<场景名>_task_<i>.db

⑤ Fixer 定向修复（fixer，温度 0.0）—— 规则/语义/建库三类问题统一交给 fixer
  └─ 给 fixer 场景名 + 任务 + 上一轮表结构 + 问题清单 → 定向修复（保持表结构不变）
  └─ 修复后再走 ②③④ 校验/建库（最多 max_retry 轮）

重试耗尽 → 放弃该任务（不写坏表兜底）
```

---

## 四、改造后的优点与创新点

### 0. 多 agent 设计：三个专职角色（职责分离 + 温度分离）

改造版把原版的「单一 prompt 设计整库」拆成**三个专职 agent**，各司其职、各配不同温度：

| 角色 | agent | 温度 | 职责 | 在流程中的位置 |
|---|---|---|---|---|
| 设计者 | schema_designer | 0.3 | 根据场景 + 任务设计整套表结构（DDL + indexes） | ① 设计 |
| 评审者 | schema_reviewer | 0.0 | SQL 语义评审（整体评审 + 逐表 verdicts） | ③ 语义评审 |
| 修复者 | fixer | 0.0 | 定向修复规则/语义/建库问题（缺主键/认证字段/字段不足/外键/SQL 错误） | ⑤ 修复 |

**设计要点**：

1. **职责分离**：设计（schema_designer）/ 评审（schema_reviewer）/ 修复（fixer）三件事由三个 agent 承担，避免一个 agent 既设计又评审又修复。
2. **温度分离**：设计偏创造（0.3），评审和修复要稳定（0.0）。
3. **分工流水线**：schema_designer 设计 → schema_reviewer 评审 → create_sqlite_database 建库 → 报错则 fixer 修复。

### 1. 「两层验证」：规则层 + 语义层（原版完全没有）

| 层 | 谁来判 | 判什么 | 成本 |
|---|---|---|---|
| 规则校验 | 代码（validators.py） | 每表有主键、无认证字段（password_hash/salt/token/session）、DDL 是合法 CREATE TABLE | 免费、确定性 |
| 语义评审 | LLM（schema_reviewer） | 整体评审 + 逐表 verdicts：漏表 / 错外键 / 字段不足 / 冗余 / 认证字段 | LLM 调用 |

原版只靠「建库失败率」判断质量，改造版先用确定性规则和语义评审在**建库之前**就筛掉问题，减少建库报错。

### 2. Fixer 定向修复 + 生成/修复分工（比原版「总结错误重试」更精准）

原版建库失败后，靠 `summarize_errors`（SQL 专家）把报错总结成避错指南，再让设计 agent 重试。改造版把「生成」和「修复」分开：**generator（schema_designer）只负责从零生成表结构，fixer 专职「基于上一轮表结构 + 问题清单」定向修复**。规则校验、语义评审、建库报错三类问题都统一交给 fixer 定向修复（而不是让 generator 整段重设计），修复更精准、收敛更快。

关键点：**fixer 拿到的不是「只有报错」，而是「上一轮完整表结构 + 问题清单」**，所以它能「看着表修」，而不是对着莫名其妙的报错从零重写。若上一轮表为空，则回到 generator 从零生成（避免 fixer 面对空表无从下手）。

### 3. 质量标准更高：要求「全部表建成功」

原版允许失败率 ≤ 10%（少量表建失败也算成功），产物里可能带坏表。改造版要求 `failed_count == 0`（全部表建成功），否则不返回。

### 4. 放弃兜底，不写坏结果

原版重试耗尽后，会挑「失败率最低」的那次结果写产物（可能仍有坏表）。改造版重试耗尽直接**放弃该场景**（返回 None，不写产物），保证产物里的每个 schema 都是完整可建的。

### 5. 每线程独立 GPTClient（并发安全）

每个任务一个线程，线程内部独立构建 agents 和 GPTClient（避免跨线程共享 httpx 连接池导致挂起）。

### 6. 任务级并发（线程池）

用 `ThreadPoolExecutor` 并发处理多个任务（`max_concurrency`）。

### 7. 任务级产物树（隔离每个任务的 db）

每个任务独立一套表结构 + 独立 .db，产物按 `(场景, 任务)` 隔离，便于下游按任务定位/修复：

```
output/<场景名>/
  task_1/
    <场景名>_task_1.db                  # 该任务的 SQLite 数据库
    <场景名>_task_1_db_schema.jsonl      # 该任务的 db_schema 那一行（与 gen_db_ma.jsonl 一致）
  task_2/...
```

---

## 五、运行方式

在**项目根目录 `ma_upgrade/`** 下执行：

```bash
python -m stages.db.generate \
  --input ma_upgrade/stages/task/output/gen_tasks_ma.jsonl
```

### 关键参数

| 参数 | 含义 |
|---|---|
| `--input` | 任务文件（第二阶段的产物 `gen_tasks_ma.jsonl`） |
| `--output` | 输出文件（默认 `ma_upgrade/stages/db/output/gen_db_ma.jsonl`，表结构 schema，每行含 `task_id`） |
| `--database_dir` | 已废弃（任务级 .db 落地到 `output/<场景名>/task_<i>/` 下，不再使用此参数） |
| `--model` | 模型名（或用环境变量 `AWM_SYN_OVERRIDE_MODEL`） |
| `--allowed_scenarios` | 只处理指定的场景 |
| `--limit` | 只处理前 limit 个任务（默认不限制） |
| `--max_retry` | 修复循环最大轮次（默认 3，即首次 generator 生成 + 最多 3 次 fixer 修复） |
| `--max_concurrency` | 任务级并发数（默认 5） |
| `--resume` | 断点续跑（默认开启，按 `(scenario, task_id)` 断点；检测到旧场景级产物自动清空重来） |

### ⚠️ 注意事项

1. **模型名**：`--model` 默认是占位符，必须用 `--model` 或环境变量 `AWM_SYN_OVERRIDE_MODEL` 覆盖，否则 `pre_process` 会 assert 失败。

2. **输入是任务文件不是场景文件**：`--input` 要传第二阶段的产物 `gen_tasks_ma.jsonl`（每行 `{scenario, tasks, angles}`），不是第一阶段的场景文件。

3. **产物有两处**：
   - `--output`（`gen_db_ma.jsonl`）保存表结构 schema，每行 `{scenario, task_id, task, db_schema, db_path}`；
   - 任务级子目录 `output/<场景名>/task_<i>/` 下，存 `.db` 数据库（`<场景名>_task_<i>.db`）和该任务的 schema 副本（`<场景名>_task_<i>_db_schema.jsonl`，内容 = `gen_db_ma.jsonl` 里那一行）。

4. **建表失败会放弃该任务**：如果经过 `max_retry + 1` 轮生成/修复后，仍无法让全部表建成功，会**放弃该任务**（不写产物），而不是写带坏表的兜底结果。一个任务失败不影响同场景其他任务。

---

## 六、一句话总结

改造版第三阶段 = **原版「单一 prompt 设计 + 失败率阈值」→ 「任务级 schema_designer 从零生成（含空表兜底）+ 规则校验 + 语义评审 + Fixer 定向修复闭环」**。核心创新：① 建库前先过「确定性规则层 + LLM 语义层」两道关；② 生成与修复分工——generator 只管从零生成，规则/语义/建库三类问题统一交给 fixer「带上上一轮表结构 + 问题清单」定向修复；③ 要求全部表建成功、重试耗尽放弃任务（不写坏表）；④ 粒度从「场景级」下沉到「任务级」——每个任务独立一套表结构 + 独立 .db，任务间互不约束，产物按 `(场景, 任务)` 隔离，便于下游按任务定位修复。
