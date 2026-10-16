"""sample 阶段（示例数据）的规则校验（确定性、非 LLM）。

只做「语法/格式」层面的确定性检查：有无 INSERT、INSERT 格式对不对、是否混入认证字段。
「表名是否在 schema」「缺表/多表」这类问题默认 generator 不会犯，交给语义评审兜底。
"""

import re   # 提取 INSERT 的列名，用于精确匹配认证字段

# 认证相关字段黑名单：INSERT 的「列名」精确等于这些才判为认证字段，避免误伤 session_duration、盐 salt 等正常数据
# 注意：TOKEN 已移除——payment/invitation/webhook token 等业务 token 太常见，一刀切误判率极高
AUTH_FIELDS = ["PASSWORD_HASH", "SALT", "SESSION"]


def rule_has_inserts(table: dict):
    """规则（已废弃）：不再要求必须有 INSERT——空表（任务要创建的对象）是合理的。"""
    if not table.get("insert_statements"):
        return "无 INSERT 语句"
    return None


def rule_insert_format(table: dict):
    """规则：每条必须是单条 INSERT 语句且以分号结尾。"""
    for stmt in table.get("insert_statements", []):
        if not isinstance(stmt, str):  # 防御：fixer 返回的 insert_statements 可能混入非字符串（如 float）
            return "存在非字符串类型的 INSERT 语句"
        s = stmt.strip()
        if not s.upper().startswith("INSERT INTO"):
            return "存在非 INSERT 语句"
        if not s.endswith(";"):
            return "INSERT 未以分号结尾"
    return None


def rule_no_auth_fields(table: dict):
    """规则：INSERT 的列名不能是认证字段（password_hash/salt/token/session，精确匹配列名）。"""
    for stmt in table.get("insert_statements", []):
        if not isinstance(stmt, str):  # 防御：非字符串交给 rule_insert_format 报告，这里跳过
            continue
        up = stmt.upper()
        m = re.search(r"INSERT\s+INTO\s+[^(]+\s*\(([^)]*)\)", up)  # 提取列名部分，只查列名不查值
        if not m:
            continue
        for col in (c.strip() for c in m.group(1).split(",")):
            if col in AUTH_FIELDS:
                return f"含认证字段 {col}"
    return None


# sample 阶段的规则清单：统一签名 rule(table) -> 问题描述或 None
# 注意：不再包含 rule_has_inserts——任务级下「任务要创建的表」应为空表（零行），空表是合理的
# （对象由 agent 运行时插入），不能误伤。「缺表」（db_schema 有但 sample_data 无 entry）由语义评审检查。
SAMPLE_RULES = [rule_insert_format, rule_no_auth_fields]


def validate_sample(sample_data: dict, rules=None):
    """对 sample_data 的每张表依次应用规则，返回 (保留的表, 被剔除的 [{name:xxx, reasons:xxx}])。"""
    if rules is None:
        rules = SAMPLE_RULES
    kept = []
    dropped = []
    for table in sample_data.get("tables", []):
        reasons = []
        for rule in rules:
            problem = rule(table)   # 执行规则，返回问题描述或 None
            if problem:
                reasons.append(problem)
        if reasons:
            dropped.append({"name": table.get("table_name", "unknown"), "reasons": reasons})
        else:
            kept.append(table)
    return kept, dropped
