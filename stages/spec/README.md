# spec 阶段：任务级 API 规范与环境文件清单

本阶段为每个任务设计完整 RESTful API 规范，并生成 env 实现所需的固定文件清单和公共模块契约。`ma_upgrade/` 是独立项目根目录。

## 流程与 Agent 分工

1. 按 `(scenario, task_id)` 关联 task、db、sample。只有 sample 保留且有对应 db 的任务进入生成。
2. **SpecPlanner** 根据当前任务、完整数据库结构和示例数据，规划最少必要 API 分组，主业务分组排在第一位。不按每张相关表机械拆分。
3. **SpecDesigner** 按分组并行生成接口。每个请求包含任务、完整数据库结构、示例数据和当前规划的分组清单。第一分组负责跨资源业务流程，其余分组只设计独立能力；没有必要接口时可返回空列表。分组名称由程序冻结。
4. **规则检查** 检查接口结构、方法、参数位置、认证接口，以及跨分组的重复 `operation_id` 和 method/path。URL 的每个 `{placeholder}` 必须有同名、不可空且 `required=true` 的 path 参数，path 参数也不能脱离 URL 单独存在；拒绝空 tags 和列表中缺名或重名的参数。
5. **SpecReviewer** 只有全部分组通过规则检查且没有缺失分组时，才审查完整规范的任务覆盖、业务规则和数据库一致性。必须同时返回逐项 `coverage` 证据清单，程序核对接口、参数、查询响应字段和依赖顺序。允许跨表事务；多余但正确的接口仅作建议，不单独导致失败。请求异常或返回格式不完整时，只重试 Reviewer；重试耗尽记录失败，不将评审格式问题交给 Fixer 改代码。
6. **SpecFixer** 只修复出错分组，看到其他分组的完整规范和最新修复结果。空 `endpoints` 表示删除冗余分组，下一轮重新校验剩余接口并检查全局任务覆盖，不重新生成已删除的分组；不能删除最后一个非空分组。真正缺失的分组重新调用 Designer 生成，仍按完整规划清单确定主业务归属，并传入已经生成的接口，避免将缺失分组误当作主分组。
7. 全部通过后，程序根据最终分组重新生成并校验 `files`，将完整 `api_spec`、`files`、`module_contract` 一起落盘。

文件清单由确定性代码生成，不增加独立 Architect，不要求模型输出源码或自由规划目录。每个分组对应一个 router，避免不同 Generator 对文件结构作出不同决定。完整参数、响应、描述和业务规则不会在汇总时截断。

## 输入输出

| 配置 | 默认路径 | 内容 |
| --- | --- | --- |
| `input_task` | `output/task/gen_tasks_ma.jsonl` | 场景级任务列表，按顺序编号 `task_1`、`task_2` 等 |
| `input_db` | `output/db/gen_db_ma.jsonl` | 任务级 `db_schema`、`db_path` |
| `input_sample` | `output/sample/gen_sample_ma.jsonl` | 任务级初始样本，决定保留哪些任务 |
| `output` | `output/spec/gen_spec_ma.jsonl` | 成功任务的完整规范和文件清单 |
| `bad_output` | `output/spec/spec_bad_tasks.jsonl` | 本次失败任务及原因 |

成功记录包含 `scenario`、`task_id`、`task`、`api_spec`、`files`、`module_contract` 和 `task_coverage`。后者记录最终通过评审的覆盖证据；environment 仍读取原来的 API 规范和文件清单。`api_spec` 保持 `api_groups` 与完整 endpoints 的结构。下面是一个分组对应的文件清单示例：

```json
[
  {"path": "main.py", "kind": "app"},
  {"path": "database.py", "kind": "database"},
  {"path": "models.py", "kind": "orm"},
  {"path": "schemas.py", "kind": "schemas"},
  {"path": "routers/__init__.py", "kind": "package"},
  {
    "path": "routers/group_001.py",
    "kind": "router",
    "group_name": "Orders",
    "operation_ids": ["list_orders", "cancel_order"]
  }
]
```

router 文件按分组顺序编号，不用业务名称组成文件路径。每个接口恰好属于一个 router。Fixer 修改接口后会重新计算清单；`files` 不包含源码，也不替代完整 API 规范。

每个 endpoint 还必须包含两个范围字段：

- `user_scope`：`current_user` 表示只查询当前用户的数据，`all` 表示管理查询或全量查询。是否存在 `user_id` 字段不能单独决定范围。
- `example_policy`：固定为 `illustrative`。所有 `example` 只说明数据形状，绝不是任务答案或必须返回的数据库结果，环境代码必须查询实时数据库。

`response` 使用递归的内联类型结构：对象必须有 `type: "object"`、`properties` 和 `required`；数组必须有 `type: "array"` 和 `items`；可空字段使用 `["类型", "null"]`。禁止使用裸字段映射、`$ref`、`anyOf`、`oneOf` 和 `allOf`。统计接口必须写清分组键、去重规则、分子、分母、百分比倍数、舍入方式和零分母行为。这样 env 阶段可以按确定的契约生成 Pydantic 模型，不需要猜测响应层级。

`module_contract` 固定以下约定：

- `database.py` 导出 `Base`、`engine`、`SessionLocal`、`get_db`，从 `DATABASE_PATH` 读取数据库路径。
- `models.py` 根据 `db_schema` 实现 ORM，统一导入 `database.Base`。
- `schemas.py` 根据完整 `api_spec` 实现请求和响应模型。
- 各 router 导出 `router`，使用 `database.get_db`；只能导入公共模块，禁止互相导入 router。
- `main.py` 导出 `app`，注册所有 router。

跨资源操作可以访问多个已有表，其事务与业务影响必须在接口描述中写清楚。具体 ORM 类名和请求响应类名由后续 env 实现确定，不能仅凭文件清单猜测。本次修改只在 spec 中产出这份契约，后续 env 重写时再落实执行。

## 运行方式

在 `ma_upgrade/` 下运行。以 PowerShell 为例，使用实际模型服务地址和名称：

```powershell
$env:AWM_SYN_LLM_PROVIDER = "openai"
$env:OPENAI_BASE_URL = "http://127.0.0.1:8000/v1"
$env:OPENAI_API_KEY = "dummy"
$env:AWM_SYN_OVERRIDE_MODEL = "你的模型名称"

# 重新生成试运行任务；关闭续跑会重建成功清单。
python -m stages.spec.generate --resume false --limit 3 --max_concurrency 1

# 之后继续生成剩余任务。
python -m stages.spec.generate --max_concurrency 2
```

旧产物没有有效 `files` 或 `module_contract` 时，续跑会明确报错；使用 `--resume false` 重新生成，不会把旧记录当作新格式的成功任务。

如果旧产物有新格式的 `files/module_contract`，但不符合当前接口规则，续跑会先完整备份成功文件到同目录的 `gen_spec_ma.resume_backup_<随机后缀>.jsonl`，再原子替换成功文件，只保留合格记录。不合格任务重新进入本次待处理队列，无需清空全部合格产物。文件清单本身被改坏时仍明确报错，不猜测清单内容。

Designer 和 Reviewer 还会逐项检查任务能力和内部 ID 的可获取性：能选择已有地址不等于能录入新地址；提供截止日期和提醒 CRUD 不等于配置推荐人数量和角色；rollout 只看到任务与 API 工具，不能把示例 ID 当真实答案。上述业务检查由 LLM 完成，仍需通过 env 的实际 rollout 验证。

## 配置

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `model` | 需指定 | 模型名称，可由 `AWM_SYN_OVERRIDE_MODEL` 覆盖 |
| `limit` | 不限制 | 跳过已完成任务后处理前 N 个任务 |
| `allowed_scenarios` | 不限制 | 场景过滤 |
| `max_retry` | 3 | 首轮检查失败后最多修复次数，每次修复后必须重新检查 |
| `plan_retry` | 2 | 规划失败后的重试次数 |
| `review_retry` | 1 | Reviewer 请求或返回格式失败后的额外重试次数，只重试评审 |
| `max_concurrency` | 15 | 任务级并发，任务内部的 API 分组也会并行请求 |
| `max_tokens` | 50000 | 每个 Agent 单次输出 token 上限，可通过 CLI 修改 |
| `sample_per_table` | 2 | 每表提供的示例 INSERT 数量，不限制 API 规范长度 |
| `resume` | true | 跳过成功清单中已完成的任务 |

成功任务逐条追加写入，支持中断续跑；失败清单只记录本次运行。Ctrl+C 沿用项目统一取消机制。

### Planner 失败诊断

Planner 推荐返回 `{"groups": ["Orders"]}`；等价的名称数组 `["Orders"]` 会直接归一化。
`groups` 必须为非空数组，每项为非空字符串；不会把数组元素的业务含义丢弃后重新请求。
程序不会将缺失字段、错误类型、空响应和请求异常统称为“未规划任何 group”。
控制台会记录任务标识、原始响应前 2000 字符、解析后的分组和每次失败原因；
完整响应保存在输出文件同级的 `_raw_llm/<场景>-<task_id>/planner_attempt_N.txt`。
规划重试会携带上一轮错误，让模型针对返回格式进行修正。
最终失败记录的 `reason` 保留各次错误和原始响应目录；不会用默认分组掩盖失败。

### 逐项任务覆盖证据

Reviewer 将任务拆成具体要求，检查每项所需的修改、过滤、返回字段、确认和保留约束。每项记录负责分组、是否覆盖、按执行顺序排列的接口，以及参数来源和业务证据。

例如退货要求可以引用 `find_orders → create_return`：外部订单号来自任务，内部 `order_id` 来自 `find_orders` 的 `orders[].id` 响应。记录参数来源时使用真实请求字段路径，例如 `body.order_id`、`body.items[].product_id`。必填对象不能只用一条泛泛的 `body` 来源掩盖内部缺失字段。

程序会执行以下检查：

- 覆盖清单非空，各项包含明确的要求、负责分组、布尔结论和业务证据。
- 引用的接口、请求参数和查询响应字段确实存在，接口归属与负责分组一致。
- 已覆盖操作链的所有必填输入都有来源记录，包含查询接口自己的必填输入。
- 查询来源在使用其结果的接口之前执行，禁止自引用和循环依赖；引用字段的声明类型与目标输入类型相容。
- `covered=false` 或证据核对失败，会覆盖模型给出的 `pass=true`，将具体问题交给对应分组的 Fixer。

`source` 只允许 `task`、`lookup`、`context`：任务中的明确值/可确定推导的值、已有查询接口的返回、环境明确约定的上下文。示例值、数据库 seed 行的位置及凭空猜测的 ID 都不是合法的业务来源。程序能验证引用和依赖，**不能确定自然语言任务是否被模型完整拆分，也不能证明 task/context 的解释或接口业务语义正确**；这些仍依赖语义评审和下游 rollout。

每次评审的解析结果、核对判决或格式错误保存到 `_raw_llm/<场景>-<task_id>/review_round_N_attempt_M.json`。控制台打印覆盖项数量和具体失败原因；最终成功产物的 `task_coverage` 保存核对通过的证据，修复后必须重新评审，不沿用上一轮证据。

现有成功记录不会仅因缺少新增的 `task_coverage` 而被自动重生成。要验证新流程，可将试运行输出写到独立文件，避免影响已有产物：

```powershell
python -m stages.spec.generate --limit 3 --max_concurrency 1 --output output/spec/spec_coverage_trial.jsonl --bad_output output/spec/spec_coverage_trial_bad.jsonl
```

## 文件与验证

| 文件 | 职责 |
| --- | --- |
| `generate.py` | Config/pre_process、任务关联、Agent 编排、修复循环、增量落盘 |
| `prompts.py` | 四个角色的提示词和统一模块约定 |
| `validators.py` | 单组及跨分组接口规则检查 |
| `coverage.py` | 逐项任务覆盖证据、参数来源和查询执行链核对 |
| `file_plan.py` | 固定文件清单、公共契约、文件归属检查和输出组装 |
| `test_generate.py` | 离线覆盖文件映射、修复、评审错误、输出和续跑 |
| `test_coverage.py` | 离线覆盖业务漏项、虚构引用、必填输入、查询依赖和评审重试 |

```powershell
python -m unittest stages.spec.test_generate stages.spec.test_coverage -v
```

这些检查确认规范结构、文件归属和部分语义审查流程，不能保证未来生成的源码可运行。源码的导入、路由注册、工具调用及数据库运行测试仍由 env 阶段负责。
