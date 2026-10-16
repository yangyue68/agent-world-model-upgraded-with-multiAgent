# env 阶段：任务级环境生成与运行检查

本阶段为每个任务生成独立的 FastAPI + SQLAlchemy 环境代码包。环境完成静态检查、导入检查、HTTP 冒烟测试和真实 rollout 探针后，才写入 `output/env/gen_envs_ma.jsonl`。每个任务按 `(scenario, task_id)` 与上游产物对应。

env 检查环境是否可运行，并保留执行轨迹。下游 verifier 使用轨迹、初始快照和数据库 diff 判断任务是否完成；env 合格不等于执行 rollout 的模型完成了任务。

## 输入

默认路径相对于独立项目根目录 `ma_upgrade/`，程序会解析成绝对路径。

| 输入 | 默认文件 | 用途 |
| --- | --- | --- |
| `input_scenario` | `output/scenario/gen_scenario_ma.jsonl` | 场景业务描述 |
| `input_task` | `output/task/gen_tasks_ma.jsonl` | 上游任务文件，启动时检查存在 |
| `input_db` | `output/db/gen_db_ma.jsonl` | 任务级数据库结构、数据库路径 |
| `input_sample` | `output/sample/gen_sample_ma.jsonl` | 任务级初始数据，供生成代码和 rollout 建库使用 |
| `input_spec` | `output/spec/gen_spec_ma.jsonl` | 任务级 API 规范、任务文本和任务编号 |

待处理任务以 spec 记录为入口，与 scenario、db、sample 关联。缺少对应产物的任务跳过。HTTP 测试使用对应 db 文件的临时副本；真实 rollout 根据 db 与 sample 产物恢复独立的初始库和最终库。

## 生成与修复流程

1. **Architect 规划文件。** 输出 `{files: [{path, purpose}]}`；检查相对路径、重复路径，以及 `main.py`、`database.py` 是否存在。`database.py` 必须定义共享的 `Base`，因为训练运行时会直接导入它。
2. **CodeGenerator 生成完整代码包。** 输出 `{files: [{path, content}]}`。所有文件仍在同一次 LLM 请求中生成，拆分文件不会绕过模型的输出长度限制。
3. **确定性补丁。** 检查前执行 `runner.prepare_files()`：补包标记文件、常见缺失 import、部分错误的顶层模块相对 import、SQLite 硬编码连接；修正 SQLAlchemy 日期类型误用，补响应模型的 `from_attributes` 配置。明确的字符串 `check_constraint` 转成 `CheckConstraint`，保留约束；其他无法确定含义的错误交给 fixer。
4. **静态检查。** 检查输出形状与 Python 语法、ORM 列名和上游 schema 的一致性、跨文件导入符号、缺失模块、非法 Column 参数，以及 ORM 类被用作 `response_model` 等错误。错误的响应模型必须修正为适当的 Pydantic 模型。
5. **导入检查。** 在临时目录和数据库副本上，用独立子进程加载 `main.app`、`database.Base` 并生成 OpenAPI。捕获导入、装饰器注册和 OpenAPI 生成阶段的报错，超时则终止进程。
6. **启动与 HTTP 冒烟测试。** 在新临时目录复制数据库，设置 `DATABASE_PATH`、`HOST`、`PORT` 后启动 `python main.py`。轮询 `/openapi.json`，子进程退出时立即返回错误。测试无路径参数的 GET，以及带 requestBody example 且无路径参数的 POST/PUT/PATCH。4xx 本身不作为环境崩溃；5xx 和传输错误会阻止通过。
7. **Fixer 定向修复。** 返回修改文件的完整内容；补丁先校验再合并。每次合并都重新执行静态、导入和运行检查。空补丁、原样返回、只改注释或空白、循环回旧版本都会结束本轮，并把失败信息交给整包重新生成。
8. **真实 rollout 探针。** 持久化候选环境，为每条探针准备独立数据库，实际调用工具并保存轨迹。发现环境问题时局部修复，再运行检查和探针。可通过 `llm_env_check` 启用 LLM 对轨迹中环境问题的补充判断；只接受布尔 `has_env_bug=true` 且问题包含轨迹证据的回复，中文“环境代码没有问题”不会被误判为故障。环境工具列表为空时明确报错。
9. **写入结果。** 通过后追加合格记录；失败记录写入坏任务文件，并清理该任务的环境与轨迹目录。

服务器日志直接写临时文件，避免 stdout 管道无人读取时被大量日志堵住。只有冒烟测试全部失败信息都是传输错误时，才使用**新服务器和全新数据库副本**复测。已出现的确定性 5xx 不会因其他请求超时而被丢弃；写请求不会在同一个数据库副本上盲目重发。

这些检查覆盖常见代码错误，但不保证所有接口和业务分支都正确。必填 query 参数、带路径参数的接口、没有示例的写接口主要由真实 rollout 覆盖；轨迹也只能覆盖实际执行过的分支。

## 输出

| 位置 | 内容 |
| --- | --- |
| `output/env/gen_envs_ma.jsonl` | 合格任务，每行包含 `scenario`、`task_id`、`task`、`db_path`、`env_dir`、`traj_dir` |
| `output/env/env_generate_bad.jsonl` | 本次失败任务和原因；新一轮运行会清空旧坏任务记录 |
| `output/env/envs/<场景名>_<task_id>/` | 任务独立代码包，含 main/database/models/schemas/routers 等文件 |
| `output/env/trajectory/<场景名>_<task_id>/probe_<编号>/` | `traj.json`、`initial.db`、`final.db` |
| `output/env/_raw_llm/<场景任务标签>/` | 各 agent 原始回复 `.txt` 和诊断元数据 `.json`，用于核对转义、token 数与截断 |

指定自定义 `output` 时，`_raw_llm` 位于该输出文件的同级目录。它是诊断记录，失败任务清理时也会保留；可通过 `--save_raw_responses false` 关闭保存。

## 运行方式

在 `ma_upgrade/` 下运行。以下是 PowerShell 示例，模型服务地址和模型名使用实际配置：

```powershell
$env:AWM_SYN_LLM_PROVIDER = "openai"
$env:OPENAI_BASE_URL = "http://127.0.0.1:8000/v1"
$env:OPENAI_API_KEY = "dummy"
$env:AWM_SYN_OVERRIDE_MODEL = "你的模型服务名"

# 先处理四个尚未完成的任务，限制同时处理两个任务
python -m stages.env.generate --limit 4 --max_concurrency 2

# 批量运行，默认续跑并跳过合格清单中的任务
python -m stages.env.generate --max_concurrency 2
```

默认输入路径都已配置，也可以通过 `--input_spec`、`--input_db` 等参数覆盖。`--limit` 在跳过已经完成的任务之后截取待处理任务，单位是任务。

## 常用配置

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `model` | 占位值，必须指定 | 主模型，可用 `AWM_SYN_OVERRIDE_MODEL` 覆盖 |
| `max_tokens` | 50000 | Architect、CodeGenerator、Fixer 每次请求的输出 token 上限 |
| `max_retry` | 2 | 整包重新生成次数，不含首次，最多生成三份代码包 |
| `max_fix_retry` | 4 | 每个静态或运行检查闭环内的 fixer 最大调用次数 |
| `plan_retry` | 2 | 规划不通过时的重试次数，不含首次 |
| `max_concurrency` | 12 | 任务并发数；试运行示例显式设为 2 |
| `resume` | true | 跳过合格输出中的任务 |
| `import_check_timeout` | 25 秒 | 导入检查超时 |
| `startup_timeout` | 30 秒 | HTTP 服务就绪超时 |
| `smoke_timeout` | 15 秒 | 每次冒烟 HTTP 请求超时 |
| `smoke_retry` | 1 | 仅传输错误时，在新数据库副本上的复测次数 |
| `save_raw_responses` | true | 保存各 agent 原始回复及用量诊断 |
| `sample_per_table` | 2 | 交给代码生成器的每张表样本 INSERT 数量 |
| `rollout_count` | 2 | 每个任务的真实探针轨迹数量，必须大于零 |
| `max_probe_retry` | 2 | 真实探针发现环境问题后的修复次数 |
| `probe_model` | 跟随主模型 | 探针使用的模型 |
| `max_steps` | 18 | 每条探针最大步数 |
| `max_new_tokens` | 50000 | 探针每次模型回复的 token 上限 |
| `llm_env_check` | true | 用 LLM 补充判断轨迹是否暴露环境问题 |
| `strict_format` | true | 探针模型回复必须使用约定协议标签 |
| `clean_orphans` | true | 清理不在合格清单中的环境和轨迹目录 |

重试次数分开计算：`max_retry` 控制整包重生成，`max_fix_retry` 控制局部修复，`max_probe_retry` 控制探针修复。后一次生成会携带前一次失败原因。

## token、字符数与截断诊断

`max_tokens=50000` 限制的是 token 数；日志的 `64836 chars` 是 `len(回复文本)` 的字符数，两者单位不同。仅凭字符数不能认定超过 token 上限。

当前日志同时打印 `completion_tokens` 与 `finish_reason`。诊断 JSON 也记录请求上限、字符数、输入和输出 token 用量。如果服务未返回 usage，相应字段为 null，不能当作零。`finish_reason=length` 表示接口因长度限制结束，此输出会被拒绝并进入重试。

去掉整个回复的 JSON 代码围栏后严格解析，仅对解码器明确指出的非法反斜杠单引号补充 JSON 转义层，保留 Python 源码里的反斜杠。其余歧义或残缺输出拒绝并重试；不使用 `json_repair` 猜测源码内容。随后仍需逐文件 Python 语法检查。fixer 输入采用带行号的原始源码，报错文件优先展示，输出仍是包含完整修改文件的 JSON。

进程内 rollout 使用 FastAPI 的实际 `operationId`（含自动生成的 `unique_id`），展开 OpenAPI 中请求模型的本地引用，并保留字段类型、nullable 和数组等约束。运行时同时支持同步、异步路由，并按 `response_model` 校验响应，把 ORM 实例转成字段数据；响应验证失败会作为环境 500 交给修复流程。

## 文件职责与离线验证

| 文件 | 职责 |
| --- | --- |
| `generate.py` | Config、输入关联、任务并发、各检查与修复闭环、轨迹及结果落盘 |
| `llm.py` | env 专用 agent 调用、原始输出和 token 诊断、JSON 解析与截断识别 |
| `prompts.py` | 规划、代码生成、修复三个角色的提示词 |
| `validators.py` | 文件形状、路径、语法、ORM 列名校验 |
| `static_check.py` | 跨文件 AST 检查、临时子进程导入检查 |
| `runner.py` | 确定性补丁、统一落盘、服务启动、冒烟测试与日志提取 |

离线回归测试不调用在线 LLM，会启动临时本地服务，数据库写入都发生在临时副本中：

```powershell
python -m unittest stages.env.test_static_check stages.env.test_generate stages.rl.test_env -v
```

运行期间按 Ctrl+C 会请求取消。导入检查、服务等待与修复轮次都会检查取消状态；主入口沿用项目统一的中断与子进程清理机制。
