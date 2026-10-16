"""db 阶段（数据库表结构）的规则校验（确定性）。"""

import re

# 认证相关字段黑名单：只匹配「列名精确等于」这些词的字段（避免把 session_id / sessions 等业务字段误伤）
# 注意：token 已从黑名单移除——payment/invitation/webhook token 等业务 token 太常见，一刀切误判率极高
AUTH_FIELD_NAMES = ["password", "password_hash", "salt", "session"]


def rule_valid_ddl(table: dict):
    """规则：DDL 必须是 CREATE TABLE 或 CREATE VIEW 语句。返回问题描述（None 表示通过）。"""
    ddl = (table.get("ddl") or "").strip()   # 取 DDL 文本并去掉首尾空白；拿不到则为空串
    up = ddl.upper()
    if not (up.startswith("CREATE TABLE") or up.startswith("CREATE VIEW")):   # 转大写后判断是否以 CREATE TABLE / CREATE VIEW 开头
        return "DDL 不是 CREATE TABLE/CREATE VIEW 语句"
    return None   # None 表示这条规则通过


def rule_has_primary_key(table: dict):
    """规则：每张表必须有主键（视图 VIEW 例外，视图无需主键）。"""
    ddl = (table.get("ddl") or "").upper()   # 取 DDL 并转大写，便于大小写不敏感匹配
    if ddl.startswith("CREATE VIEW"):        # 视图无需主键，跳过
        return None
    if "PRIMARY KEY" not in ddl:             # 没有主键声明
        return "缺主键"
    return None


def rule_no_auth_fields(table: dict):
    """规则：不能含认证字段（列名精确等于 password/salt/token/session）。

    用「列名精确匹配」而不是「子串匹配」，避免把 session_id、sessions 表、
    session_status 等业务字段误判成认证字段（session 在很多场景是业务概念，如诊疗会话）。
    """
    ddl = (table.get("ddl") or "").strip()
    m = re.search(r"CREATE TABLE\s+[`\"]?\w+[`\"]?\s*\((.*)\)", ddl, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    col_names = set()
    for part in m.group(1).split(","):
        toks = part.strip().split()
        if toks:
            col_names.add(toks[0].strip('`"').lower())
    for field in AUTH_FIELD_NAMES:
        if field in col_names:
            return f"含认证字段 {field.upper()}"
    return None


# db 阶段的规则清单：把上面 3 条规则打包，供 validate_tables 调用
DB_RULES = [rule_valid_ddl, rule_has_primary_key, rule_no_auth_fields]


def validate_tables(tables: list, rules):
    """对每张表依次应用所有规则，返回 (保留的表, 被剔除的 [{table, reasons}])。"""
    kept = []      # 通过所有规则的表
    dropped = []   # 被剔除的表及其原因
    for table in tables:
        reasons = []          # 当前表命中了哪些规则
        for rule in rules:
            problem = rule(table)   # 执行规则，返回问题描述或 None
            if problem:
                reasons.append(problem)
        if reasons:
            # 有规则未通过 → 归入被剔除列表，并记录所有原因
            dropped.append({"table": table.get("name", "unknown"), "reasons": reasons})
        else:
            # 全部规则通过 → 保留
            kept.append(table)
    return kept, dropped

