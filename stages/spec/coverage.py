"""核对 Reviewer 的任务覆盖证据；业务语义仍由模型判断，不推断数据库 ID。"""

from copy import deepcopy


class ReviewFormatError(ValueError):
    """评审材料格式不完整，应重试 Reviewer，不能据此修改业务接口。"""


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _params(endpoint):
    params = endpoint["request_params"]
    return params if isinstance(params, dict) else {p["name"]: p for p in params}


def _required_fields(schema, prefix, *, parameter=False):
    """展开必填对象字段和数组元素，用 body.order_id / body.items[].id 引用。"""
    kinds = schema["type"]
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if "object" in kinds:
        names = schema.get("required_fields" if parameter else "required", [])
        if names:
            return [field for name in names for field in
                    _required_fields(schema["properties"][name], f"{prefix}.{name}")]
    elif "array" in kinds:
        return _required_fields(schema["items"], prefix + "[]")
    return [prefix]


def _field_schema(schema, path):
    """解析已声明响应字段；$ 表示响应本身，数组元素以 [] 表示。"""
    if path == "$":
        return schema
    current = schema
    for segment in path.split("."):
        arrays = 0
        while segment.endswith("[]"):
            segment = segment[:-2]
            arrays += 1
        if segment:
            current = current.get("properties", {}).get(segment)
        if not isinstance(current, dict):
            return None
        for _ in range(arrays):
            kinds = current.get("type")
            if kinds != "array" and not (isinstance(kinds, list) and "array" in kinds):
                return None
            current = current.get("items")
            if not isinstance(current, dict):
                return None
    return current


def _input_schema(endpoint, path):
    name, _, rest = path.partition(".")
    arrays = 0
    while name.endswith("[]"):
        name = name[:-2]
        arrays += 1
    schema = _params(endpoint).get(name)
    if schema is None:
        return None
    suffix = "[]" * arrays
    if rest:
        suffix += ("." if suffix else "") + rest
    return _field_schema(schema, suffix) if suffix else schema


def validate_review(api_groups, reply):
    """返回带覆盖材料的分组判决；不成立的证据强制覆盖模型的 pass=true。"""
    if not isinstance(reply, dict):
        raise ReviewFormatError("评审结果必须是含 results 和 coverage 的对象")
    groups = {g["group_name"] for g in api_groups}
    verdicts = reply.get("results")
    if not isinstance(verdicts, list) or any(
        not isinstance(v, dict) or not isinstance(v.get("group"), str)
        or type(v.get("pass")) is not bool or not isinstance(v.get("issue", ""), str)
        for v in verdicts
    ):
        raise ReviewFormatError("results 必须包含 group、布尔 pass 和字符串 issue")
    if len(verdicts) != len(groups) or {v["group"] for v in verdicts} != groups:
        raise ReviewFormatError("results 遗漏、重复或包含未知分组")
    if any(not v["pass"] and not _nonempty(v.get("issue")) for v in verdicts):
        raise ReviewFormatError("results 中 pass=false 必须说明具体 issue")
    coverage = reply.get("coverage")
    if not isinstance(coverage, list) or not coverage:
        raise ReviewFormatError("coverage 必须是非空逐项任务覆盖清单，不能只返回 pass=true")
    for index, row in enumerate(coverage, 1):
        if (not isinstance(row, dict) or not _nonempty(row.get("requirement"))
                or not isinstance(row.get("group"), str) or row["group"] not in groups
                or type(row.get("covered")) is not bool
                or not isinstance(row.get("operation_ids"), list)
                or any(not _nonempty(op) for op in row["operation_ids"])
                or len(set(row["operation_ids"])) != len(row["operation_ids"])
                or not _nonempty(row.get("evidence")) or not isinstance(row.get("issue", ""), str)
                or not isinstance(row.get("inputs"), list)):
            raise ReviewFormatError(f"coverage 第 {index} 项缺少有效 requirement/group/covered/operation_ids/evidence/inputs")
        if not row["covered"] and not _nonempty(row.get("issue")):
            raise ReviewFormatError(f"coverage 第 {index} 项未覆盖，必须给出具体 issue")
        if row["covered"] and not row["operation_ids"]:
            raise ReviewFormatError(f"coverage 第 {index} 项宣称已覆盖但未引用任何接口")
        for item in row["inputs"]:
            if (not isinstance(item, dict) or not _nonempty(item.get("operation_id"))
                    or not _nonempty(item.get("parameter"))
                    or item.get("source") not in ("task", "lookup", "context")
                    or not _nonempty(item.get("detail"))):
                raise ReviewFormatError(f"coverage 第 {index} 项 inputs 缺少有效 operation_id/parameter/source/detail")
            if item["source"] == "lookup" and (
                not _nonempty(item.get("lookup_operation_id")) or not _nonempty(item.get("response_field"))
            ):
                raise ReviewFormatError(f"coverage 第 {index} 项 lookup 来源必须包含 lookup_operation_id 和 response_field")

    endpoints = {ep["operation_id"]: ep for g in api_groups for ep in g["endpoints"]}
    owners = {ep["operation_id"]: g["group_name"] for g in api_groups for ep in g["endpoints"]}
    result = deepcopy(verdicts)
    verdict_map = {v["group"]: v for v in result}
    for v in result:
        v["coverage"] = []
    covered_operation_inputs = {}
    # A task-provided array is a single request value.  Requiring the reviewer to
    # repeat every leaf of an array-of-objects (items[].name, items[].quantity, ...)
    # creates false negatives without improving API validation.  Keep the strict
    # leaf-level check for lookup-sourced arrays, where an internal id still needs an
    # explicit source and response field.
    covered_operation_task_arrays = {}
    covered_operation_groups = {}
    for row in coverage:
        verdict = verdict_map[row["group"]]
        verdict["coverage"].append(deepcopy(row))
        problems = []
        if not row["covered"]:
            problems.append(f"任务要求未覆盖：{row['issue']}")
        ops = row["operation_ids"]
        for op in ops:
            if op not in endpoints:
                problems.append(f"引用了不存在的接口 {op}")
        if row["covered"] and not any(owners.get(op) == row["group"] for op in ops):
            problems.append(f"引用接口不属于负责分组 {row['group']}，请明确实际业务归属")
        seen = set()
        for item in row["inputs"]:
            op, parameter = item["operation_id"], item["parameter"]
            key = (op, parameter)
            if key in seen:
                problems.append(f"参数来源重复：{op}.{parameter}")
            seen.add(key)
            if op not in ops:
                problems.append(f"参数证据引用未列入执行链的接口 {op}")
            if op not in endpoints or _input_schema(endpoints[op], parameter) is None:
                problems.append(f"引用了不存在的请求参数 {op}.{parameter}")
            if item["source"] != "lookup":
                continue
            lookup = item["lookup_operation_id"]
            if lookup not in endpoints:
                problems.append(f"参数 {op}.{parameter} 的查询来源接口 {lookup} 不存在")
            else:
                response_schema = _field_schema(endpoints[lookup]["response"], item["response_field"])
                input_schema = _input_schema(endpoints[op], parameter) if op in endpoints else None
                if response_schema is None:
                    problems.append(f"查询接口 {lookup} 未声明响应字段 {item['response_field']}")
                elif input_schema is not None:
                    input_types = input_schema["type"]
                    output_types = response_schema["type"]
                    input_types = set(input_types if isinstance(input_types, list) else [input_types]) - {"null"}
                    output_types = set(output_types if isinstance(output_types, list) else [output_types]) - {"null"}
                    if "number" in input_types:
                        input_types.add("integer")
                    if not output_types or not output_types <= input_types:
                        problems.append(f"查询字段 {lookup}.{item['response_field']} 的类型与参数 {op}.{parameter} 不匹配")
            if lookup not in ops or op not in ops or ops.index(lookup) >= ops.index(op):
                problems.append(f"参数 {op}.{parameter} 的来源 {lookup} 必须在执行链中先执行，不能自引用或循环依赖")
        if row["covered"]:
            for op in ops:
                if op not in endpoints:
                    continue
                # Register the operation even when this row provides no
                # parameter evidence.  A later union check must still report
                # missing required inputs for an operation with zero evidence.
                covered_operation_inputs.setdefault(op, set())
                covered_operation_groups.setdefault(op, set()).add(row["group"])
                covered_operation_inputs[op].update(
                    (item["operation_id"], item["parameter"])
                    for item in row["inputs"]
                    if item["operation_id"] == op
                )
                for item in row["inputs"]:
                    if item["operation_id"] != op or item["source"] == "lookup":
                        continue
                    parameter = item["parameter"]
                    schema = _input_schema(endpoints[op], parameter)
                    if isinstance(schema, dict):
                        kinds = schema.get("type")
                        kinds = kinds if isinstance(kinds, list) else [kinds]
                        if "array" in kinds and "." not in parameter and not parameter.endswith("[]"):
                            covered_operation_task_arrays.setdefault(op, set()).add(parameter)
        if problems:
            verdict["pass"] = False
            detail = f"覆盖检查「{row['requirement']}」：" + "; ".join(problems)
            verdict["issue"] = "\n".join(filter(None, (verdict.get("issue"), detail)))

    # The same operation can serve several requirement rows in one workflow.
    # Check its required inputs once against the union of evidence from those rows.
    for op, evidenced in covered_operation_inputs.items():
        endpoint = endpoints.get(op)
        if endpoint is None:
            continue
        missing = []
        for name, param in _params(endpoint).items():
            if not param.get("required"):
                continue
            for path in _required_fields(param, name, parameter=True):
                covered_by_task_array = any(
                    path.startswith(array_name + "[].") or path == array_name + "[]"
                    for array_name in covered_operation_task_arrays.get(op, set())
                )
                if (op, path) not in evidenced and not covered_by_task_array:
                    missing.append(path)
        if missing:
            detail = "覆盖检查：" + "; ".join(
                f"必填参数 {op}.{path} 没有可获取的来源证据" for path in missing
            )
            for group_name in covered_operation_groups[op]:
                verdict = verdict_map[group_name]
                verdict["pass"] = False
                verdict["issue"] = "\n".join(filter(None, (verdict.get("issue"), detail)))
    return result
