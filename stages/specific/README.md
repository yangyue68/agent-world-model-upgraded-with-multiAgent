# specific：独立新版任务级 API 规范阶段

在 `ma_upgrade` 根目录运行 `python -m stages.specific.generate`。
本包保留多角色协作，只设计当前任务需要的 API。旧 `stages/spec` 原样保留；
本包不导入旧包，也不生成 `files`、`module_contract` 或 `task_coverage`。

## 流程和角色分工

1. **关联输入**：以 sample 成功文件为任务名单，用精确的 `(scenario, task_id)`
   关联 task 和 db。核对三份产物中已有的任务文本，防止任务列表调整后错配。
   缺记录、缺数据库结构或任务文本不一致的记录写入失败文件；重复任务键直接报错。
2. **Planner**：看到当前任务、完整数据库结构和部分样本，返回最少必要的 API 分组名称。
   通常一个主业务分组就够；不会按数据库表数量机械拆分，也不规划源文件。
3. **Designer**：同一任务的分组按顺序生成。每次都看到完整任务、数据库结构、
   全部分组名称以及已经生成的完整接口，减少重复能力和内部 ID 无法查询的问题。
   第一组必须非空；后续组若完全被已有接口覆盖，可以返回空数组，随后仍会做全局评审。
4. **确定性结构检查**：检查接口必需字段、HTTP 方法、全局重复 operation_id、
   等价路径、URL 占位符、path 参数必填性、递归请求/响应类型和用户范围字段。
   不通过时先修复，避免 Reviewer 对残缺结构做无意义评审。
5. **Reviewer**：读取完整规范，对每个分组输出 `group/pass/issue`。
   检查明确任务能力、查询条件、返回字段、内部 ID 获取方式、数据库表/列引用、
   用户范围和业务规则。没有逐字段 coverage 证据协议。
   提示词明确禁止凭空增加发布、提交、确认等步骤；额外但正确的接口或风格偏好不构成淘汰理由。
6. **Fixer**：收到原始输入、完整当前规范和具体错误，只返回出错分组的完整替换内容。
   可以增加缺失接口和参数，不允许空结果删除分组，也不把部分补丁当作完整分组。
   每组修复立即更新全局规范，后续 Fixer 看到最新状态。
7. **重新检查并落盘**：每次修复后重新做结构检查和全局语义评审，包括最后一次修复。
   通过后逐行追加成功产物；耗尽次数后记录具体失败阶段和原因。

任务之间并发，同一任务内部串行。因此任务并发参数直接控制同时活跃的角色请求数量，
不再叠加分组级并发。Ctrl+C 沿用 `lib.interrupt` 的取消机制。

这不是通过放宽业务正确性来保证成功：缺少必需能力仍会失败。
但数据库引用和自然语言业务是否正确由 Reviewer 判断，程序不能从描述文字中可靠证明它们。
最终实现是否可运行、执行后状态是否正确，仍需后续单文件环境生成和 rollout 验证。

## 输入输出

| 参数 | 默认路径 | 内容 |
| --- | --- | --- |
| `input_task` | `output/task/gen_tasks_ma.jsonl` | 场景级 `scenario/tasks`，按原顺序对应 `task_1` 等 |
| `input_db` | `output/db/gen_db_ma.jsonl` | 任务级 `db_schema` |
| `input_sample` | `output/sample/gen_sample_ma.jsonl` | 成功任务名单、初始快照或样本 INSERT |
| `output` | `output/specific/gen_spec_ma.jsonl` | 本版本生成并通过检查的 API 规范 |
| `bad_output` | `output/specific/spec_bad_tasks.jsonl` | 本轮输入异常或生成失败记录 |

成功记录形式：

```json
{
  "scenario": "Shop",
  "task_id": "task_1",
  "task": "任务原文",
  "spec_version": "specific-v1",
  "api_spec": {
    "api_groups": [
      {"group_name": "Products", "endpoints": []}
    ]
  }
}
```

上例仅展示层级；真实成功记录的每个分组都有非空的完整 endpoints。
分组仍用于组织业务能力，不代表未来环境中的文件数量。

endpoint 保留 `method/path/operation_id/summary/description/user_scope/request_params/response`。
`tags` 和 `example_policy` 可由程序补为分组标签和 `illustrative`。
description、任务文本、DDL、请求字段和响应字段都不截断。
样本优先取 `initial_snapshot` 的列名和每表前 N 行，并带总行数；没有快照时取每表前 N 条 INSERT。
样本仅帮助理解数据形状，不代表完整数据库、标准答案或 rollout 已知的内部 ID。
本阶段不打开或修改任何 SQLite 文件。

失败记录保留 `scenario/task_id/task/stage/reason`。
阶段包括 `input/planner/designer/reviewer/validation/exception`，
区分输入问题、模型响应问题和业务检查失败。

## 运行方式

先激活已有 conda 环境，在 `ma_upgrade` 目录下设置实际服务配置：

```powershell
$env:AWM_SYN_LLM_PROVIDER = "openai"
$env:OPENAI_BASE_URL = "http://你的服务器地址:端口/v1"
$env:OPENAI_API_KEY = "EMPTY"
$env:AWM_SYN_OVERRIDE_MODEL = "你的服务模型名称"

# 先试 3 个任务；默认输出在 output/specific，不混用旧 spec 产物。
python -m stages.specific.generate --limit 3 --max_concurrency 1

# 继续生成，自动跳过本版本的成功记录。
python -m stages.specific.generate --max_concurrency 4
```

支持 `--input_task`、`--input_db`、`--input_sample` 指向其他输入。
`--allowed_scenarios '["完整场景名"]'` 使用场景名精确匹配。
`--limit` 在筛选、关联及跳过成功记录后，选取前 N 个待生成任务。
输入异常单独统计，不计入模型生成的成功率分母。

默认 `resume=true`。续跑仅接受 `specific-v1` 且结构有效、任务键不重复的成功记录；
不会把旧 spec 文件直接当作新版本结果。
`--resume false` 会清空指定成功输出并重新生成；失败文件每轮覆盖，仅记录本轮问题。
`--limit 0` 不调用模型，但仍初始化输出并写入检测到的输入异常。

**当前旧 `stages/environment` 仍要求 files/module_contract，不能直接消费本包的新产物。**
待单文件环境阶段完成后，再让其读取 `output/specific/gen_spec_ma.jsonl`。

## 配置

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `max_concurrency` | 4 | 并发任务数 |
| `max_retry` | 3 | 首次检查失败后最多修复轮数 |
| `plan_retry` | 2 | Planner 请求/格式失败的额外重试次数 |
| `generation_retry` | 1 | Designer、Fixer 请求/外层格式失败的额外重试次数 |
| `review_retry` | 1 | Reviewer 请求/格式失败的额外重试次数 |
| `max_tokens` | 16000 | Designer/Fixer 单次最大输出 token |
| `plan_max_tokens` | 2048 | Planner 最大输出 token |
| `review_max_tokens` | 4096 | Reviewer 最大输出 token |
| `request_timeout` | 1200 秒 | 传给共享模型客户端的请求超时 |
| `sample_per_table` | 2 | 提供给角色的每表样本行数/INSERT 数 |
| `save_raw_responses` | true | 保存每次模型响应及完整提示词 |

请求失败重试还会经过共享 `GPTClient` 自身的传输重试机制。
Reviewer 格式失败只重试 Reviewer，不把格式错误交给 Fixer 改业务。
`finish_reason=length` 等非正常结束会明确拒绝；不会用 JSON 修补器猜出缺失内容。
支持完整 `<think>...</think>` 前缀及外围 JSON 代码围栏。

控制台记录任务、角色、耗时、字符数、completion tokens、finish reason、校验问题和修复应用情况。
启动时显示续跑跳过、输入淘汰、本轮待处理及受 limit 限制留待后续的数量。
每完成一个任务，显示本轮完成数/总数、百分比、通过数、淘汰数、未完成数和累计耗时。
每条淘汰日志包含场景、task_id、失败阶段和原因；结束时按阶段汇总。
本轮进度仅计算本轮实际提交生成的任务，输入淘汰和历史成功任务单独统计。
完整响应保存在成功输出同级 `_raw_llm/<任务标识>/`，`.txt` 是原文，`.json` 包含提示词和响应元信息。
token 和字符不是同一单位，不能直接比较 `max_tokens` 与字符数。

## 文件分工和验证

| 文件 | 职责 |
| --- | --- |
| `generate.py` | CLI、并发调度、增量落盘 |
| `config.py` | Config 和 pre_process |
| `storage.py` | 输入精确关联、JSONL 读写、续跑检查 |
| `pipeline.py` | 单任务四角色流程、检查和修复循环 |
| `llm.py` | 模型调用、响应解析、日志、有限重试 |
| `prompts.py` | 四角色提示词 |
| `validators.py` | 接口结构、响应协议和无歧义归一化 |
| `schema.py` | 递归类型与示例校验，自包含副本 |
| `test_pipeline.py` | 离线流程回归测试 |

```powershell
python -m unittest stages.specific.test_pipeline -v
```

离线测试验证流程、修复传递、格式拒绝和持久化，不代表真实模型的生成成功率。
