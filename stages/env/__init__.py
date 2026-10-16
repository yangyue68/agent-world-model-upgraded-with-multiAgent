"""env 阶段：真正的多 agent 多文件环境代码生成（流水线第 6 步的升级版）。

与 env 阶段（单文件 server_code.py）的区别：
  - Architect 先规划代码包的文件结构（models/schemas/routers/main 等）；
  - CodeGenerator 按文件清单一次性生成多个文件的代码（结构化 JSON）；
  - 落盘后真正起服务器测试 + endpoint 冒烟测试；
  - Fixer 局部修复（只重写出错的几个文件），而不是整段重写。
"""

