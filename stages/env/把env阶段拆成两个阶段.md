# 把env阶段拆成两个阶段

## 阶段一、规划文件清单

1、整理输入，把上游的产物（场景描述、任务描述、db_schema、sample、spec）按任务级组织好，记为input

2、Architect规划文件清单,记为files

3、对文件清单进行规则校验，如果检查出错误，记为problems1，

4、把input和files交给reviewer进行评审，如果reviewer反馈有问题，记为problems2

5、合并problems1和problems2，结果记为problems，若problems为空，结束。否则，把input+files+problems一起交给fixer进行修复。修复返回的结果回到第3步，重新进行规则校验和LLM语义评审，直到重试次数达到上限（淘汰该任务）或者problems完全消除为止



## 阶段二：生产env

1、整理输入，把上游的产物（场景描述、任务描述、db_schema、sample、spec、files清单）按任务级组织好，记为input

2、generator生产具体文件，并填充到files清单的各个文件里

3、对产物文件进行规则校验，如果检查出错误，记为problems1。

4、启动环境和数据库，进行冒烟测试，如果环境启动失败，或冒烟测试有问题，记下错误详情，记为problems2

6、合并problems1和problems2，记为problems。如果为空，进入下一步，否则，把input+产物+problems交给fixer进行修复。修复返回的结果回到第3步，重新进行规则校验和冒烟测试，直到重试次数达到上限（淘汰该任务）或者problems完全消除为止

8、第7步结束后还能留下来的任务，大概率没有问题，接下来跑rollout，得到轨迹、initial_db、final_db

9、扫描轨迹里的5xx类的报错（不用LLM）、以及LLM对轨迹进行检查（用LLM)，如果都认为环境没有问题，就保留该环境，落地代码，否则不再修复，直接淘汰。因为规则校验出错，是确定性的错误，fixer多半能修复，而LLM评审出错，比较难修复，而且评审自己也可能出错





你认为我的方案可以吗？有没有什么建议？