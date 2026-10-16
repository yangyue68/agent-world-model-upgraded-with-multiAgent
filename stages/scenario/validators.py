"""scenario 阶段（场景生成）的规则校验（确定性、非 LLM）。

对齐 ma_upgrade 的「两层验证」设计：
  - 规则校验（本文件）：名称合法、描述长度、无 GUI/下载关键词、精确去重、类别归一化。
  - 语义评审（reviewer，LLM）：可仿真性、具体性、多样性。

原则：能程序化验证的绝不让 LLM 评（省钱、稳定）。
"""

from __future__ import annotations

from typing import Callable, List

# GUI / 直接交互类关键词：场景描述强调「下载/截图/点击/拖拽/上传文件」等纯界面操作时，
# 说明核心价值依赖 GUI，不适合 API 仿真，应剔除。
GUI_KEYWORDS = [
    "download", "screenshot", "drag and drop", "scroll",
    "upload a file", "upload files", "file upload", "click on",
    "下载", "截图", "点击", "拖拽", "上传文件",
]

MIN_NAME_LEN = 2        # 场景名最短长度
MAX_NAME_LEN = 80       # 场景名最长长度
MIN_DESC_LEN = 50       # 描述最短长度（字符数；太短信息不足）
MAX_DESC_LEN = 3000     # 描述最长长度（字符数；太长冗余）

# 类别别名归一化：LLM 可能输出 "ecommerce"/"e-commerce"/"e_commerce" 等不同写法，
# 统一归一到标准类别名，避免 category_counts 统计与 max_per_category 上限被"别名"打散。
CATEGORY_ALIASES = {
    "ecommerce": "E-commerce/Marketplace",
    "e-commerce": "E-commerce/Marketplace",
    "e_commerce": "E-commerce/Marketplace",
    "marketplace": "E-commerce/Marketplace",
    "retail": "E-commerce/Marketplace",
    "auction": "E-commerce/Marketplace",
    "booking": "Booking/Reservation",
    "reservation": "Booking/Reservation",
    "appointment": "Booking/Reservation",
    "travel": "Booking/Reservation",
    "hospitality": "Booking/Reservation",
    "social": "Social/Community",
    "community": "Social/Community",
    "forum": "Social/Community",
    "dating": "Social/Community",
    "task management": "Task/Project Management",
    "project management": "Task/Project Management",
    "collaboration": "Task/Project Management",
    "workflow": "Task/Project Management",
    "finance": "Finance/Banking",
    "banking": "Finance/Banking",
    "investment": "Finance/Banking",
    "payment": "Finance/Banking",
    "cryptocurrency": "Finance/Banking",
    "subscription": "Subscription/Membership",
    "membership": "Subscription/Membership",
    "loyalty": "Subscription/Membership",
    "inventory": "Inventory/Catalog",
    "catalog": "Inventory/Catalog",
    "warehouse": "Inventory/Catalog",
    "asset tracking": "Inventory/Catalog",
    "messaging": "Messaging/Communication",
    "communication": "Messaging/Communication",
    "customer support": "Messaging/Communication",
    "ticketing": "Messaging/Communication",
    "scheduling": "Scheduling/Calendar",
    "calendar": "Scheduling/Calendar",
    "event management": "Scheduling/Calendar",
    "healthcare": "Healthcare/Medical",
    "medical": "Healthcare/Medical",
    "telehealth": "Healthcare/Medical",
    "pharmacy": "Healthcare/Medical",
    "education": "Education/Learning",
    "learning": "Education/Learning",
    "lms": "Education/Learning",
    "tutoring": "Education/Learning",
    "real estate": "Real Estate/Property",
    "property": "Real Estate/Property",
    "rental": "Real Estate/Property",
    "hr": "HR/Recruiting",
    "recruiting": "HR/Recruiting",
    "job board": "HR/Recruiting",
    "payroll": "HR/Recruiting",
    "legal": "Legal/Compliance",
    "compliance": "Legal/Compliance",
    "contract": "Legal/Compliance",
    "logistics": "Logistics/Shipping",
    "shipping": "Logistics/Shipping",
    "fleet": "Logistics/Shipping",
    "delivery": "Logistics/Shipping",
    "food": "Food/Restaurant",
    "restaurant": "Food/Restaurant",
    "ordering": "Food/Restaurant",
    "fitness": "Fitness/Wellness",
    "wellness": "Fitness/Wellness",
    "workout": "Fitness/Wellness",
    "automotive": "Automotive",
    "car dealership": "Automotive",
    "iot": "IoT/Smart devices",
    "smart home": "IoT/Smart devices",
    "device management": "IoT/Smart devices",
}


def normalize_category(value) -> str:
    """把单个类别名归一化：压缩空白、统一大小写、尽量对齐标准类别名。"""
    cat = " ".join(str(value or "").strip().split())
    return CATEGORY_ALIASES.get(cat.casefold(), cat)


def normalize_categories(values) -> list:
    """把类别列表归一化 + 去重（保留前 5 个）。"""
    if not isinstance(values, list):
        values = [values] if values else []
    out = []
    seen = set()
    for v in values:
        cat = normalize_category(v)
        key = cat.casefold()
        if cat and key not in seen:
            seen.add(key)
            out.append(cat)
    return out[:5]



def _contains_any(text: str, keywords: List[str]) -> bool:
    """判断 text 里是否包含任意一个关键词（不区分大小写）。"""
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def rule_name_valid(scenario: dict):
    """规则：场景名非空、长度合理。返回问题描述（None 表示通过）。"""
    name = (scenario.get("name") or "").strip()
    if not name:
        return "场景名为空"
    if len(name) < MIN_NAME_LEN:
        return "场景名过短"
    if len(name) > MAX_NAME_LEN:
        return "场景名过长"
    return None


def rule_desc_length(scenario: dict):
    """规则：描述长度在合理范围内。"""
    desc = (scenario.get("description") or "").strip()
    if not desc:
        return "描述为空"
    if len(desc) < MIN_DESC_LEN:
        return "描述过短（信息不足）"
    if len(desc) > MAX_DESC_LEN:
        return "描述过长"
    return None


def rule_no_gui(scenario: dict):
    """规则：描述不含 GUI/下载/直接交互类关键词。"""
    text = f"{scenario.get('name', '')} {scenario.get('description', '')}"
    if _contains_any(text, GUI_KEYWORDS):
        return "描述含 GUI/下载/直接交互类关键词"
    return None


# 场景生成阶段的规则清单
SCENARIO_RULES: List[Callable] = [rule_name_valid, rule_desc_length, rule_no_gui]


def dedup_scenarios(scenarios: List[dict]) -> List[dict]:
    """按场景名精确去重（小写、去空格），保留原始顺序。"""
    seen = set()
    out: List[dict] = []
    for s in scenarios:
        key = (s.get("name") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(s)
    return out


def validate_scenarios(scenarios: List[dict], rules=None):
    """对每个场景依次应用规则，返回 (保留的场景, 被剔除的 [{scenario, reasons}])。"""
    if rules is None:
        rules = SCENARIO_RULES
    kept: List[dict] = []
    dropped: List[dict] = []
    for s in scenarios:
        reasons = []
        for rule in rules:
            problem = rule(s)
            if problem:
                reasons.append(problem)
        if reasons:
            dropped.append({"scenario": s.get("name", "?"), "reasons": reasons})
        else:
            kept.append(s)
    return kept, dropped

