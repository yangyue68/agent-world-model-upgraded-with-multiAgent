# Verifier 阶段

Verifier 紧接 environment 阶段。当前 environment 对每个任务只保存一条最终
rollout，因此 verifier 也只处理一条 traj.json，不扫描 probe_0、probe_1
目录，也不聚合多条轨迹。

## 职责

本阶段只做以下工作：

1. 从 output/environment/gen_envs_ma.jsonl 读取已经生成环境的任务。
2. 按 (scenario, task_id) 找到唯一环境目录、traj.json、initial.db 和
   final.db。
3. 使用 lib/db_diff.py 计算精确的数据库状态变化。
4. 把任务、完整轨迹、初始快照和 diff 交给 LLM judge。
5. 生成 RL 使用的任务名单。

本阶段不会启动环境、重放工具调用、生成任务专属验证函数，也不会根据
final_answer 标签、写操作关键词或 diff 是否为空在本地猜测任务结果。

## 输入

默认输入：

    output/environment/gen_envs_ma.jsonl
    output/sample/gen_sample_ma.jsonl
    output/environment/trajectory/<场景>_task_<N>/
      traj.json
      initial.db
      final.db

environment 清单中的 traj_dir 优先于默认轨迹目录。每条 environment 记录
只允许对应一个 traj.json。缺少轨迹、数据库、任务文本或任务键不合法时，
会记录为 evidence_error，不会调用 judge。

initial.db 优先作为初始快照来源。只有旧产物缺少它时，才使用 sample 中的
initial_snapshot 兼容回退。最终数据库必须存在。

## 分类规则

单个任务只有一个分类：

| 分类 | 含义 | 输出名单 |
| --- | --- | --- |
| complete | judge 确认任务完成 | solvable_tasks.jsonl |
| incomplete | 证据有效，但任务未完成 | unsolvable_tasks.jsonl |
| agent_error | Agent 调用或参数错误，环境证据有效 | unsolvable_tasks.jsonl |
| server_error | 环境或服务端错误 | verifier_bad_tasks.jsonl |
| evidence_error | 轨迹、数据库或对应关系不可用 | verifier_bad_tasks.jsonl |
| judge_error | judge 请求失败或返回格式非法 | verifier_bad_tasks.jsonl |

incomplete 和 agent_error 只有在证据完整时才进入 unsolvable。服务端错误、
材料错误和 judge 错误不会被伪装成 Agent 的负例。

## 输出

所有清单都通过临时文件和 os.replace 原子替换：

| 文件 | 内容 |
| --- | --- |
| gen_verifier_ma.jsonl | 每个 environment 任务一行的完整结果 |
| solvable_tasks.jsonl | judge 判定 complete 的任务 |
| unsolvable_tasks.jsonl | judge 判定 incomplete 或 agent_error 的任务 |
| verifier_bad_tasks.jsonl | 材料、服务端或 judge 错误任务 |
| rl_ready_tasks.jsonl | solvable 与 unsolvable 的并集 |

名单中的 task_id 是 RL 使用的 0 基整数，同时保留 task_key（如 task_1）。
主结果还保留分类、状态、轨迹路径、数据库路径、judge 结果和证据缓存路径。

完整的 judge 输入和结果保存在：

    output/verifier/judge_evidence/<规范化场景>/<task_key>/
      judge_input.json
      judge_result.json

## 断点续传

默认 resume=True：

- 任务完成后立即更新五份输出清单；
- judge 输入和结果先写临时文件，再原子替换；
- 已有相同模式、相同输入指纹且已经得到四种有效 judge 分类的任务直接复用；
- evidence_error 和 judge_error 不作为最终断点结果，下次运行会重新检查；
- Ctrl+C 后已完成任务和 judge 缓存会保留，重新运行即可继续。

如果希望完全重新评判，使用 --resume false。这不会删除旧证据目录，但会
忽略旧结果并重建五份清单。

## 运行

在 ma_upgrade 目录执行：

    python -m stages.verifier.generate --model <judge-model> --max_concurrency 5

也可以设置：

    $env:AWM_SYN_LLM_PROVIDER = "openai"
    $env:AWM_SYN_OVERRIDE_MODEL = "<judge-model>"

日志会显示总任务数、材料错误数、逐任务进度、完成/未完成/坏任务计数和最终
汇总。max_concurrency 只控制不同任务之间的并发；一个任务内部不存在额外
probe 循环。

## 与 RL 的关系

Verifier 的结果是离线任务白名单，不直接充当 RL reward：

- solvable_tasks.jsonl 可用于先训练已有 rollout 能完成的任务；
- unsolvable_tasks.jsonl 可用于困难任务；
- verifier_bad_tasks.jsonl 不应作为 RL 白名单；
- RL 每次新的 rollout 仍会重新计算数据库 diff 并调用同一套 judge，得到本次
  rollout 的 reward。
