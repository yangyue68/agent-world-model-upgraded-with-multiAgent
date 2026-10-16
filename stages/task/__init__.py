"""task 阶段：任务生成（从 task 拆分出 angle 后的独立阶段，流水线第 3 步）。

输入：场景（gen_scenario_ma.jsonl）+ 角度（gen_angles_ma.jsonl）
子模块：
- prompts：本阶段的提示词（任务生成 / 任务评审 / fixer 修复）
- validators：本阶段的规则校验（确定性）
- generate：任务生成主逻辑
"""

