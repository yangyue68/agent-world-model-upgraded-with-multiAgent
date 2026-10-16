# Verifier：用 LLM judge 判断 env rollout 是否完成任务

本阶段紧接 env 阶段。它读取 env 已保存的轨迹和数据库状态，调用 LLM judge 判断 rollout 是否真正完成任务，再把任务分成「至少一次完成」和「所有尝试都未完成」两组。

处理范围由 `output/environment/gen_envs_ma.jsonl` 决定：记录必须有已存在的环境目录，以及至少一个 `traj.json`。没有环境或轨迹的任务直接跳过。

## 输入与任务对应关系

`generate.py` 使用 `Config` 和 `pre_process()` 管理参数。项目根目录是 `ma_upgrade` 本身，默认路径以及自定义相对路径都以它为基准。

主要输入为：

```text
output/environment/gen_envs_ma.jsonl
output/environment/envs/single-file-v1/<任务及运行标识>/round_N/
output/environment/trajectory/single-file-v1/<任务及运行标识>/round_N/
  probe_0/
    traj.json
    initial.db
    final.db
  probe_1/
    ...
```

- `gen_envs_ma.jsonl` 提供 `scenario`、`task_id`、`task`、`env_dir`、`traj_dir`。
- `traj.json` 中的 `messages` 是完整执行轨迹，已经包含模型的最终回答。顶层重复的 `final_answer` 字段不再额外传给 judge。
- 优先从每条 probe 的 `initial.db` 读取初始快照，与同目录 `final.db` 比较。这保证 judge 看到该次 rollout 实际经历的状态，即使 sample 后来发生变化也不影响历史评判。
- 旧产物如果没有 `initial.db`，才从 `output/sample/gen_sample_ma.jsonl` 中按规范化场景名和 `task_id` 查找 `initial_snapshot`，并要求任务文本一致。sample 不决定本阶段的处理范围。
- 缺少 `final.db`、缺少可用初始状态或轨迹损坏时，记录材料错误，不将未知状态当作无变化。

## 处理流程

1. **选择 env 任务**：读取 env 清单，只保留环境与轨迹已落地的任务，再应用 `allowed_scenarios` 和 `limit`。任务按场景名、任务编号自然排序。
2. **准备每条轨迹的证据**：读取初始快照及最终库，调用 `lib/db_diff.py` 的 `diff_snapshots()`。judge 收到完整变化内容，而非仅行数摘要；没有数据或 schema 变化时传 `diff: null`。
3. **调用 judge**：复用 `lib/llm_judge.py`，输入任务、轨迹、初始快照和 diff。初始快照和 diff 不做截断，也不在这里重复执行 sample 的大小淘汰策略。
4. **保存逐条结果**：保留 judge 的分类、理由和证据。每条 probe 都要评判，即使前一条已经完成，也继续记录其他尝试的结果。
5. **汇总任务并分组**：按下面的规则写出主结果及任务名单。任务一完成评判就增量落盘；正常结束时对清单排序。

本阶段复用已有 rollout，不启动 env，不让 judge 查询数据库，也不重放工具调用。

## Judge 分类与任务分组

| 逐条分类 | 含义 |
| --- | --- |
| `complete` | judge 根据回答和数据库证据确认任务完成 |
| `incomplete` | 任务未完全完成 |
| `agent_error` | 模型的调用、推理等错误导致未完成 |
| `server_error` | 环境故障导致未完成 |
| `judge_error` | 裁判请求/解析失败，或缺少评判材料；不能得出任务结论 |

`final_answer` 标签、模型自称成功、工具返回成功都不是完成证明。只读任务要核对回答；写入任务要核对数据库效果；混合任务要核对两者。只写任务可以在轨迹中给出简短的完成说明，无须编造查询结果。

**任务汇总规则：**

- 任一 probe 为 `complete`：`status="completed"`、`completed=true`，写入 `solvable_tasks.jsonl`。
- 所有 probe 都获得有效分类且没有 `complete`：`status="not_completed"`、`completed=false`，写入 `unsolvable_tasks.jsonl`。
- 没有 `complete`，同时至少一条 probe 评判失败：`status="judge_error"`、`completed=null`，暂不进入上述两组。

一个任务可能同时有成功轨迹和 judge 错误：已成功的轨迹足以将任务归入完成组，错误轨迹仍会记入错误清单，续跑时重试。`solvable/unsolvable` 是兼容下游的文件名，表示现有 rollout 的完成表现，不证明任务本身是否可解。

## 输出

默认输出位于 `output/verifier/`：

| 文件 | 内容 |
| --- | --- |
| `gen_verifier_ma.jsonl` | 每个实际参与评判的 env 任务一行：任务状态、各分类计数和逐 probe judge 结果 |
| `solvable_tasks.jsonl` | 至少一条 rollout 完成的任务 |
| `unsolvable_tasks.jsonl` | 所有 rollout 都未完成的任务 |
| `rl_ready_tasks.jsonl` | 上述两个已确定分组的并集，可作为 RL 的 `allowed_tasks_path` |
| `verifier_bad_tasks.jsonl` | 每条材料错误或 judge 错误的记录，包含场景、任务、probe 和错误原因 |
| `judge_evidence/<规范化场景名>/<task_N>/<probe>/judge_input.json` | 完整 judge 输入，含轨迹、初始快照和 diff |
| 同目录 `judge_result.json` | 该条 judge 结果、输入指纹和模型配置相关的缓存信息 |

所有输出清单中的 `task_id` 统一为 RL 使用的 **0 基整数**，`task_key` 保存上游的字符串 ID。例如上游 `task_1` 对应 `task_id=0, task_key="task_1"`。项目内部文件路径以相对路径保存，便于打包迁移。

`gen_verifier_ma.jsonl` 可能包含尚未得出结论的 `judge_error`；训练时选择 `rl_ready_tasks.jsonl` 或两个分组文件。

## 配置参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--input_env` | `output/environment/gen_envs_ma.jsonl` | 上一阶段的成功产物清单 |
| `--input_sample` | `output/sample/gen_sample_ma.jsonl` | 仅作任务文本/初始快照兜底 |
| `--trajectory_root` | `output/environment/trajectory` | env 记录没有 `traj_dir` 时按任务名定位 |
| `--model` | `your-llm-model-name` | 必须显式配置，或使用 `AWM_SYN_OVERRIDE_MODEL` |
| `--provider` | `azure` | 项目 GPTClient 的 provider；`AWM_SYN_LLM_PROVIDER` 可覆盖 |
| `--max_concurrency` | `5` | 并行处理任务数；每个任务内部逐条处理 probe |
| `--max_retry` | `2` | judge 请求/回答无效时的额外重试次数；GPTClient 自身也有网络重试 |
| `--request_timeout` | `600` | 单次请求超时，单位秒 |
| `--max_tokens` | `4096` | judge 回答的 token 上限，不是证据长度上限 |
| `--resume` | `True` | 复用相同输入、模型和提示词对应的成功 judge 结果 |
| `--allowed_scenarios` | `None` | 可选场景列表 |
| `--limit` | `None` | 限制本次选择的 env 任务数；默认处理全部，不按试运行数据量写死 |
| `--evidence_dir` | `output/verifier/judge_evidence` | 保存评判证据及续跑缓存的位置 |

五个输出清单路径分别用 `--output`、`--bad_output`、`--ready_output`、`--solvable_output`、`--unsolvable_output` 覆盖。

`resume=True` 会重新扫描本次选定的 env 任务，重新准备证据并校验内容指纹；同一轨迹和数据库状态未变化时复用成功结果。失败的评判会重试，新增或变更的 probe 会重新评判，旧版「出现 final_answer 就算成功」的清单不会被当作 judge 缓存。`--resume False` 强制重新调用 judge。

每次生成的清单只反映本次选中的 env 任务，包括使用 `limit` 或场景过滤时。小范围试跑如果需要保留已有全量清单，应指定不同输出路径。完整 judge 缓存独立保存在 `evidence_dir` 中。

## 运行方式

在项目根目录 `ma_upgrade` 中，使用原有 `awm2` 环境和已有的模型服务环境变量：

```powershell
python -m stages.verifier.generate --model <裁判模型名>
```

若已经配置 `AWM_SYN_OVERRIDE_MODEL`，可直接运行：

```powershell
python -m stages.verifier.generate
```

显式指定输入和并发数：

```powershell
python -m stages.verifier.generate `
  --input_env output/environment/gen_envs_ma.jsonl `
  --input_sample output/sample/gen_sample_ma.jsonl `
  --model <裁判模型名> `
  --max_concurrency 5
```

模型服务沿用 `lib/gpt.py` 的配置：OpenAI 兼容接口使用 `AWM_SYN_LLM_PROVIDER=openai`、`OPENAI_BASE_URL`、`OPENAI_API_KEY`；Azure 使用 `AZURE_ENDPOINT_URL` 和 `AZURE_OPENAI_API_KEY`。

验证无需调用模型的流程测试：

```powershell
python -m unittest stages.verifier.test_generate -v
```
