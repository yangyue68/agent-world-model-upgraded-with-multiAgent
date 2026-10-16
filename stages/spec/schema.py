"""校验 spec 使用的内联类型契约及示例；不修改、推断或截断业务字段。"""

import math


TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def _matches_type(value, kind):
    return {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: type(value) is int,
        "number": lambda: type(value) is int or (type(value) is float and math.isfinite(value)),
        "boolean": lambda: type(value) is bool,
        "null": lambda: value is None,
    }[kind]()


def validate_value(value, schema: dict, path: str = "example") -> list[str]:
    """检查已通过结构校验的 schema 的示例/默认值，包括嵌套必填和可空性。"""
    kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    if not any(_matches_type(value, kind) for kind in kinds):
        return [f"{path}: 值类型与 {kinds} 不符"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: 值不在 enum 中"]
    errors = []
    if isinstance(value, dict):
        for name in schema["required"]:
            if name not in value:
                errors.append(f"{path}: 缺少必填字段 {name}")
        properties = schema["properties"]
        for name, item in value.items():
            if name in properties:
                errors.extend(validate_value(item, properties[name], f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: 未定义字段 {name}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(validate_value(item, schema["items"], f"{path}[{index}]"))
    return errors


def validate_schema(schema, path: str = "response", *, parameter: bool = False,
                    depth: int = 0) -> list[str]:
    """检查明确的 type/properties/items/required。参数的 required 是布尔值。"""
    if not isinstance(schema, dict):
        return [f"{path}: 类型定义必须是对象"]
    if depth > 50:
        return [f"{path}: 类型定义嵌套过深"]
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if not kinds or any(not isinstance(k, str) or k not in TYPES for k in kinds):
        return [f"{path}: 缺少有效 type，不允许直接使用字段映射"]
    if len(kinds) != len(set(kinds)) or len(set(kinds) - {"null"}) > 1:
        return [f"{path}: type 只允许单一类型或该类型与 null 的组合"]
    if any(k in schema for k in ("$ref", "anyOf", "oneOf", "allOf")):
        return [f"{path}: 请使用完整内联类型定义，不使用引用或组合 schema"]
    errors = []
    if "object" in kinds:
        props = schema.get("properties")
        required = schema.get("required_fields") if parameter else schema.get("required")
        if not isinstance(props, dict) or any(not isinstance(n, str) or not n for n in props):
            errors.append(f"{path}: object 必须定义 properties 字段对象")
        else:
            for name, child in props.items():
                errors.extend(validate_schema(child, f"{path}.properties.{name}", depth=depth + 1))
        if (not isinstance(required, list) or any(not isinstance(n, str) for n in required)
                or len(required) != len(set(required))):
            errors.append(f"{path}: object 必须定义必填字段列表 {'required_fields' if parameter else 'required'}（可为空）")
        elif isinstance(props, dict) and any(n not in props for n in required):
            errors.append(f"{path}: 必填字段必须存在于 properties")
    if "array" in kinds:
        errors.extend(validate_schema(schema.get("items"), f"{path}.items", depth=depth + 1))
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            errors.append(f"{path}: enum 必须是非空列表")
        elif any(not any(_matches_type(v, k) for k in kinds) for v in enum):
            errors.append(f"{path}: enum 值类型必须符合 type")
    if not errors:
        # 外层参数的 required 为是否必须传参；转换副本供通用示例检查使用。
        value_schema = dict(schema)
        if parameter and "object" in kinds:
            value_schema["required"] = schema["required_fields"]
        for key in ("example", "default"):
            if key in schema:
                errors.extend(validate_value(schema[key], value_schema, f"{path}.{key}"))
    return errors
