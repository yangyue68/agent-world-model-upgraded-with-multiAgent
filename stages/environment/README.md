# Environment 阶段

本阶段按任务生成可运行的单文件 FastAPI 环境。每个任务只生成一个
`main.py`，不再规划文件、不拆分 router，也不做跨文件修补。

## 流程

1. 按 `(scenario, task_id)` 合并 specific、task、db、sample 产物。
2. `EnvGenerator` 生成完整 `main.py`。
3. 在隔离进程中检查 Python、FastAPI、OpenAPI、数据库启动状态和接口契约。
4. `EnvReviewer` 检查真正影响任务的业务逻辑。
5. 从同一初始数据库副本启动 rollout，保存轨迹、初始库、最终库和 diff。
6. 明确的环境错误交给 `EnvFixer`，最多修复 3 轮；每轮从源码和启动检查重新开始。
7. 通过的环境写入 `output/environment/gen_envs_ma.jsonl`。

模型没有工具调用、参数错误或正常业务 4xx 不会被当作环境代码错误；它们会记录为
`probe_incomplete` 或交给 verifier 判断。数据库启动时发生写入、异常、5xx 或契约错误，
才会进入修复循环。

## 运行

在 `ma_upgrade` 目录执行：

```powershell
python -m stages.environment.generate --max_concurrency 1 --limit 4
```

私有模型可通过 `AWM_SYN_LLM_PROVIDER`、`OPENAI_BASE_URL`、`OPENAI_API_KEY` 和
`AWM_SYN_OVERRIDE_MODEL` 配置。需要重新生成时使用 `--resume false`。

中间诊断文件保存在 `output/environment/envs/.work`；成功发布后只保留最终环境和轨迹。
