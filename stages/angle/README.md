# 第二阶段：任务角度生成（angle）

从 task 阶段拆分出来的独立阶段，流水线第 2 步。

## 一、定位

根据第一阶段产出的「场景」（`gen_scenario_ma.jsonl`），为每个场景规划一组 **任务角度**（用户意图的分类，如 search/create/update/manage），作为第三阶段（task）生成具体任务的引导。

## 二、流程

```
Planner(规划角度) → 角度语义评审(逐条)
  ↓ 合格角度不足 → 带「上一轮角度 + 失败原因」重规划（方案 A，不整段重设计）
  ↓ 合格角度够 num_angles → 产出 {scenario, angles}
```

## 三、输入 / 输出

- 输入：`input`（场景文件 `gen_scenario_ma.jsonl`）
- 输出：`stages/angle/output/gen_angles_ma.jsonl`（每行 `{scenario, angles: [...]}`）

## 四、运行方式

在**项目根目录 `ma_upgrade/`** 下执行：

```bash
python -m stages.angle.generate \
  --input stages/scenario/output/gen_scenario_ma.jsonl
```

## 五、关键参数

| 参数 | 含义 |
|---|---|
| `--input` | 场景文件（第一阶段产物 `gen_scenario_ma.jsonl`） |
| `--output` | 输出文件（默认 `stages/angle/output/gen_angles_ma.jsonl`） |
| `--num_angles` | 每个场景保留的合格角度数（默认 5） |
| `--model` | 模型名（或用环境变量 `AWM_SYN_OVERRIDE_MODEL`） |
| `--max_retry` | 规划 + 评审循环最大轮次（默认 1） |
| `--max_concurrency` | 场景级并发数（默认 8） |
| `--resume` | 断点续跑（默认开启，output 无产物时自动重新生成） |

## 六、方案 A：带上一轮结果重规划

`--resume` 默认开启。角度评审未通过时，planner 会带上「上一轮角度 + 失败原因」重新规划（在上一轮基础上修正/替换不合格角度），而不是从零重规划。
