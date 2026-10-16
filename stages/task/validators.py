"""
通用规则校验器（RuleValidator，确定性、非 LLM）。

与 SemanticReviewer（LLM 评审）互补：
  - 规则校验器：把 prompt 里"能写成规则"的要求，用代码强制校验。快、免费、100% 准确。
  - LLM 评审：验证规则覆盖不了的语义质量（具体性、真实性、多样性）。

设计原则：能程序化验证的，绝不让 LLM 来评（省钱、稳定）。

本文件提供通用的 validate_tasks 框架 + 任务生成阶段的规则清单；
后续 db / spec / env 阶段可复用同一套框架，只需替换规则清单。
"""

from __future__ import annotations  # 类型注解兼容

from typing import Callable, List  # 类型注解用

# ---- 认证类关键词（命中则任务需要登录/注册，应剔除）----
AUTH_KEYWORDS = [
    "login", "log in", "sign in", "signin",       # 英文登录
    "logout", "log out", "sign out", "signout",   # 英文登出
    "register", "sign up", "signup",              # 英文注册
    "create an account", "password",              # 创建账号 / 密码
    "登录", "注册", "登出", "密码",                 # 中文
]

# ---- GUI / 直接交互类关键词（命中则任务需要界面操作，应剔除）----
GUI_KEYWORDS = [
    "download", "open the page", "open page", "screenshot",   # 下载 / 打开页面 / 截图
    "click", "drag", "scroll", "upload a file",               # 点击 / 拖拽 / 滚动 / 上传
    "下载", "截图", "点击", "打开页面",                          # 中文
]

MIN_TASK_LEN = 5      # 太短说明信息不完整（如只有"下单"两字）
MAX_TASK_LEN = 800    # 允许一段话，放宽上限（原"单句"要求已删除）


def _contains_any(text: str, keywords: List[str]) -> bool:
    """判断 text 里是否包含任意一个关键词（不区分大小写）。"""
    low = text.lower()                            # 统一转小写，实现大小写不敏感
    return any(k.lower() in low for k in keywords)  # 任一关键词命中即返回 True


def rule_no_auth(task: str):
    """规则：不含认证/登录/注册类关键词。返回问题描述（None 表示通过）。"""
    if _contains_any(task, AUTH_KEYWORDS):
        return "涉及认证/登录/注册"
    return None  # None 表示这条规则通过


def rule_no_gui(task: str):
    """规则：不含 GUI/下载/直接交互类关键词。"""
    if _contains_any(task, GUI_KEYWORDS):
        return "涉及 GUI/下载/直接交互"
    return None


def rule_length(task: str):
    """规则：长度在合理范围内（非空、不太短、不太长）。"""
    t = task.strip()               # 去掉首尾空白
    if not t:
        return "空任务"            # 空字符串（可能被 strip 成空）
    if len(t) < MIN_TASK_LEN:
        return "过短，信息可能不完整"
    if len(t) > MAX_TASK_LEN:
        return "过长"
    return None


# ---- 任务生成阶段的规则清单：把上面 3 条规则打包，供 validate_tasks 调用 ----
TASK_RULES: List[Callable] = [rule_no_auth, rule_no_gui, rule_length]


def dedup_tasks(tasks: List[str]) -> List[str]:
    """按内容去重（保留原始顺序）。"""
    seen = set()                  # 记录已经出现过的任务（去空格后的文本）
    out: List[str] = []
    for t in tasks:
        key = t.strip()           # 用去空格后的文本作为去重键
        if key and key not in seen:   # 非空 且 没出现过
            seen.add(key)
            out.append(t)         # 保留原始文本（不 strip），避免改动内容
    return out


def validate_tasks(tasks: List[str], rules: List[Callable]):
    """对每条 task 依次应用所有规则，返回 (保留的任务, 被剔除的 [{task, reasons}])。

    每条规则是一个函数：task -> 问题描述(str) 或 None（通过）。
    """
    kept: List[str] = []      # 通过所有规则的任务
    dropped: List[dict] = []  # 被剔除的任务及其原因
    for task in tasks:
        reasons = []          # 当前任务命中了哪些规则
        for rule in rules:
            problem = rule(task)   # 执行规则，返回问题描述或 None
            if problem:
                reasons.append(problem)
        if reasons:
            # 有规则未通过 → 归入被剔除列表，并记录所有原因
            dropped.append({"task": task, "reasons": reasons})
        else:
            # 全部规则通过 → 保留
            kept.append(task)
    return kept, dropped


def validate_tasks_paired(paired: list, rules: List[Callable]):
    """在「角度→任务」配对结构上做规则校验（保留角度，不展平）。
    返回 (保留的配对 [{"angle", "tasks"}], 被剔除的 [{angle,task, reasons}])。"""
    kept_paired: list = []
    dropped: list = []
    for p in paired:
        ok_tasks: list = []
        for task in p.get("tasks", []):
            reasons = []
            for rule in rules:
                problem = rule(task)
                if problem:
                    reasons.append(problem)
            if reasons:
                dropped.append({"angle": p.get("angle", "未知角度"), "task": task, "reasons": reasons})
            else:
                ok_tasks.append(task)
        if ok_tasks:
            kept_paired.append({"angle": p.get("angle", "未知角度"), "tasks": ok_tasks})
    return kept_paired, dropped
