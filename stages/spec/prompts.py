"""spec 多 agent 提示词；文件结构和模块契约由程序统一规定。"""

IMPLEMENTATION_CONTRACT_PROMPT = """
Implementation contract (fixed by the pipeline, do not generate Python source or a files list):
- Each API group will be implemented in exactly one routers/group_NNN.py file.
- Shared files are main.py, database.py, models.py, schemas.py, routers/__init__.py.
- database.py exposes Base, engine, SessionLocal, get_db and reads DATABASE_PATH.
- Routers expose router and use get_db; they can import database, models and schemas,
  but cannot import other routers. Any cross-resource operation belongs to one endpoint
  and may access multiple existing tables in the same transaction.
- Do not invent database tables or columns, redesign the storage schema, or duplicate
  an endpoint owned by another group. Document cross-table effects in the description.
- operation_id must be globally unique lower snake_case; prefix it with the resource
  name when needed to distinguish similar operations across groups.
- If two endpoints perform similar functions (same resource + same method) but differ
  only in path parameter naming, merge them into one canonical endpoint.
- Preserve full business rules in descriptions; do not drop rules to meet a length cap.
- Every endpoint must set user_scope to current_user or all. Use current_user only for
  personal/owned data; use all for administrative searches, all-applicant queries and
  reviewer workload reports. The existence of a user_id/reviewer_id column alone does
  not imply a current-user filter. Assume authorization is already handled externally.
- Every endpoint must set example_policy to illustrative. ALL examples show data shape
  only: they are not task answers or expected database results. Sample rows may be partial.
  Implementations must compute results from the live database, never hardcode examples.
- response must be a complete inline typed schema: object has properties and required
  (an explicit list, possibly empty); array has one items schema; nested objects/arrays
  follow the same rule. Never use bare field maps as a response or as array items.
- Use type as a single string or [type, "null"] for nullable values. Required presence
  and nullability are different: a required nullable field must be present, but may be null.
  Do not use $ref, anyOf, oneOf or allOf. Inline nested types, enums and constraints fully.
- request_params remains a name-to-parameter mapping. Each parameter has boolean required.
  Object parameters use properties plus required_fields (a list) at their outermost level;
  nested object schemas use required lists. Array parameters must have typed items.
  Example of a REQUIRED request body (not a response schema):
  "body": {"type": "object", "param_type": "body", "required": true,
           "description": "Update fields", "properties": {"note": {"type": "string"}},
           "required_fields": ["note"]}
  Never put a list in a parameter's required field. Always include param_type.
  Declare every {placeholder} in a route as a required path parameter.
  Conversely, every path parameter MUST appear as a {placeholder} in that route.
  The route string belongs ONLY in endpoint.path, e.g. endpoint.path="/orders/{{order_id}}".
  Never create request_params.path to hold route placeholders. Instead declare
  request_params.order_id={"type":"integer","param_type":"path","required":true,...}.
  If a route has multiple placeholders, define one named parameter for each placeholder.
  Nested objects, including array items, use required: ["field"] (NOT required_fields).
- For every statistic document the grouping keys, deduplication rule, numerator,
  denominator, scale (e.g. multiply by 100), rounding and zero-denominator behavior.
  Unless the task explicitly asks for a distribution among decided applications, decision
  category percentages use ALL assigned applications in that reviewer/stage group as
  the denominator. If also reporting percentages among decided applications, give them
  a separate unambiguous name such as percentages_among_decided. Never conflate them.
"""

# ---------- 规划（SpecPlanner） ----------
SPEC_PLANNER_SYSTEM_PROMPT = """You are an API architect. Given a set of user tasks and a database schema, you plan the MINIMAL set of resource groups (api_groups) that the RESTful API needs to expose to accomplish those tasks. Plan only what the tasks actually require — do NOT add resource groups for tables/features the tasks never touch."""

SPEC_PLANNER_USER_PROMPT = """Plan the resource groups (api_groups) that the API for {scenario_name} should expose.

User Tasks:
{tasks_list}

Database Schema:
{database_schema}

Sample Data (initial state, sampled rows; empty tables are created at runtime):
{sample_data}

Return ONLY JSON in this format:
{{
  "groups": ["Products", "Orders", "Cart"]
}}

Requirements:
1. Each group is a logical resource type (a table, or several closely-related tables).
2. The groups must collectively cover ALL the user tasks — and ONLY what the user tasks require. Do NOT add a group for a table/feature that no user task touches; fewer, well-chosen groups are better than many mostly-unused ones.
3. Do NOT include authentication groups (assume auth is external). Business operations
   on users/reviewers are allowed when the task requires them.
4. Use concise PascalCase group names.
5. Return a JSON object with the exact key "groups" and a non-empty array of strings.
   Do not use "api_groups" or return objects instead of names. Even read-only tasks need
   at least one business group; a cross-table operation can belong to a single group."""


# ---------- 生成（SpecDesigner） ----------
SPEC_DESIGNER_SYSTEM_PROMPT = """You are an expert API designer and backend architect. Given one resource group, the full task list, and the database schema, you design the RESTful API endpoints for that group. The endpoints must be machine-readable and agent-friendly.

Scope discipline: design ONLY the endpoints that the user tasks actually need to accomplish (directly or as a necessary intermediate step). Do NOT add extra CRUD endpoints, list-all endpoints, or convenience features the tasks never use.
Ownership rule for parallel designers: the FIRST planned group owns workflows affecting
multiple planned resources (merge, bulk assignment, approval with payment, etc.). Other
groups may provide only distinct operations on their own resource, not another version
of that entire workflow. Return endpoints=[] if this group has no independent capability
needed by the task. Do not add another group's capabilities to compensate for a presumed gap."""

SPEC_DESIGNER_USER_PROMPT = """Design the API endpoints for the resource group "{group_name}" of {scenario_name}.

User Tasks (context; focus on those related to this group):
{tasks_list}

Database Schema (context):
{database_schema}

Sample Data (initial state, sampled rows; empty tables are created at runtime):
{sample_data}


Design rules:
1. Use RESTful conventions (GET / POST / PUT / DELETE / PATCH) with proper resource paths.
2. Each endpoint performs ONE business operation; it may update several related tables
   in a single transaction. This does not violate single responsibility.
3. The API MUST map to the schema — use exact table names and column names.
4. Do NOT include authentication endpoints; assume authorization has already been handled.
5. Set user_scope='current_user' and apply user_id=1 only to personal/owned data. Administrative or all-record queries must set user_scope='all' and must not be restricted to the current user.
6. Design ONLY the endpoints the user tasks require. Follow the ownership rule for the
   ordered group list. Do not copy the whole task workflow into each resource group.
7. Cover EVERY requested change and output, not just the main action. A task that changes
   an address needs address-content inputs, not only an existing address_id. A workflow
   that configures recommendation counts and roles needs an operation for those settings.
8. The rollout agent sees the task and API tools, not the database snapshot. It cannot
   infer internal IDs from examples. For task entities named by natural names, external
   IDs, dates or codes, accept those selectors directly or expose the necessary lookup.
   Examples are illustrative and must never substitute for lookup capabilities.

Agent-friendly requirements (REQUIRED for every endpoint):
- summary: one-line purpose (<=80 chars).
- description: complete business semantics, including filters, preconditions, state changes and cross-table effects. No arbitrary length cap.
- operation_id: unique snake_case.
- tags: [group name].
- request_params: each parameter must include type, param_type (query/path/body), required, description, example.
- response: complete recursive typed schema with properties/required for objects and items for arrays.
- user_scope: current_user or all, consistent with the task and endpoint description.
- example_policy: illustrative (examples are never expected query results or task answers).
- URL routes are endpoint.path, not a request parameter. For /orders/{{order_id}}, define
  request_params.order_id with param_type="path" and required=true; never define a
  request_params.path object containing order_id.

Return ONLY JSON in this format:
{{
  "group_name": "{group_name}",
  "endpoints": [
    {{
      "path": "/api/products",
      "method": "GET",
      "summary": "List all products",
      "description": "Retrieve a list of products.",
      "operation_id": "list_products",
      "tags": ["products"],
      "request_params": {{}},
      "user_scope": "all",
      "example_policy": "illustrative",
      "response": {{
        "type": "object",
        "properties": {{
          "products": {{
            "type": "array",
            "items": {{
              "type": "object",
              "properties": {{"id": {{"type": "integer"}}, "name": {{"type": "string"}}}},
              "required": ["id", "name"]
            }}
          }}
        }},
        "required": ["products"]
      }}
    }}
  ]
}}"""


# ---------- 评审（SpecReviewer） ----------
SPEC_REVIEWER_SYSTEM_PROMPT = """You review RESTful API specifications for a web-agent environment. Check task coverage across the COMPLETE specification and identify concrete business or contract errors. Each group need only cover its own distinct scope. Cosmetic preferences and redundant but correct capabilities are advisory, not reasons to fail."""

SPEC_REVIEWER_USER_PROMPT = """Review the API specification for {scenario_name} and return a verdict for EACH resource group.

User Tasks:
{tasks_list}

Database Schema:
{database_schema}

Sample Data (initial state, sampled rows; empty tables are created at runtime):
{sample_data}

API Specification:
{api_spec_json}


Return ONLY JSON in this format:
{{
  "results": [
    {{"group": "Products", "pass": true, "issue": ""}},
    {{"group": "Orders", "pass": false, "issue": "missing endpoint to create an order"}}
  ],
  "coverage": [
    {{
      "requirement": "Find a product by the name stated in the task",
      "group": "Products",
      "covered": true,
      "operation_ids": ["search_products"],
      "inputs": [
        {{"operation_id": "search_products", "parameter": "query", "source": "task",
          "detail": "The task explicitly provides the product name/search text."}}
      ],
      "evidence": "search_products accepts query text and returns matching products from the live database.",
      "issue": ""
    }},
    {{
      "requirement": "Create an order for the selected product",
      "group": "Orders",
      "covered": false,
      "operation_ids": [],
      "inputs": [],
      "evidence": "No existing endpoint creates an order.",
      "issue": "Add an order-creation capability with all necessary task inputs."
    }}
  ]
}}

The example is only a format illustration: use the ACTUAL groups, operation_ids and
parameters of the supplied spec, never copy invented example references.
coverage must be NON-EMPTY. First enumerate ALL distinct requirements from the task,
including requested changes, filters, returned fields, confirmations and preservation
constraints. Then give one coverage row per requirement. Do not omit a requirement
because an existing endpoint cannot accomplish it. Combine only inseparable details of
one operation; never hide independent missing steps inside a vague "complete task" row.
Each row must name one existing group responsible for implementing that requirement,
even if its capability is missing. covered=false forces that group's verdict to fail.
For covered=true, operation_ids is the ordered execution chain of existing endpoints
(lookups FIRST, then operations using their results); at least one belongs to the named
responsible group. The evidence explains the actual inputs, state effects or response
fields proving this requirement. Do not claim coverage just because a summary sounds similar.

Across the coverage rows, provide an inputs entry for EVERY required input of each
endpoint used by a covered execution chain. A workflow may be split into several
requirement rows, so do not repeat an identical input merely because another row cites
the same operation. The union of those rows must contain every required input.
For a required array value whose complete contents are supplied by the task, one
task-sourced entry for the array itself is sufficient (for example `items` or
`body.items`); do not repeat every `items[].field` leaf. Keep leaf-level entries for
lookup-sourced values when an internal identifier must come from a response field.
Parameter names refer to actual request fields: use body.order_id for a
required object property, body.items[].product_id for a property of an array item,
and animal_ids[] for scalar array elements. Do not use a generic "body" entry in place
of its required properties. For objects with no required properties, the object itself
may be referenced. Optional parameters may also be listed when relevant to the task.
Each entry has operation_id, parameter, source and a non-empty detail. source is:
- task: value or natural selector is explicitly given by the task, or deterministically
  derived from it (e.g. a stated range of tags). A human name does NOT reveal an internal ID.
- lookup: value is obtained from another endpoint. Also give lookup_operation_id and
  response_field, e.g. "orders[].id" or "product.id"; "$" means the whole response.
  That endpoint and field must exist and the lookup must appear EARLIER in operation_ids.
  Include the lookup's own required inputs. Do not create self-dependencies or cycles.
- context: ONLY a value explicitly provided by the fixed environment convention,
  such as the current user identity. Explain the convention. It cannot supply arbitrary
  product/program/order IDs, seed rows, or an invented frozen reference date.
If a required input cannot be obtained, mark covered=false and explain the missing
lookup or selector capability. Examples and sample row positions are NEVER input sources.

The "results" array must contain one entry for EVERY group in the specification. A group fails if:
1. its endpoints do not cover the related user tasks (a task-required capability is MISSING from the spec — this is the most important failure),
2. endpoints use wrong paths or HTTP methods,
3. endpoints reference tables or columns not in the schema,
4. it contains authentication endpoints,
5. endpoints are missing required fields (summary / operation_id / request_params / response / user_scope / example_policy),
6. response types are not recursively explicit, nullable fields are incorrectly non-null, or examples violate their declared types,
7. a personal-user filter is imposed on a task requiring all applicants/reviewers, or user_scope contradicts the task/description,
8. a statistic has an ambiguous denominator or uses decided_total for a category percentage that should be over total_assigned. Require explicit grouping, numerator, denominator, rounding and zero-denominator behavior.
Evaluate coverage across all groups, never demand every group implement the entire task.
Before assigning pass=true, check each requested action and returned field separately.
For each action, identify the actual endpoint, input fields and state effects. If no
endpoint can express one requested change, mark its responsible group pass=false and
name the missing capability explicitly. Do not assume omitted inputs will be filled by
the implementation, examples, database seed data or an endpoint that does not exist.
In particular, selecting an existing address_id does not allow entering a new address;
deadline/reminder CRUD does not configure recommendation counts or recommender roles.
Check identifier reachability: the rollout agent receives task text and API tools, not
the database snapshot. Every required internal ID must be obtainable through an exposed
lookup or replaced by a supported natural/external selector. Reject specs that require
guessing order_item_id, return_reason_id, program_id, etc. from illustrative examples.
A cross-table transaction is a valid business operation; do not reject it for updating
several related resources. Distinct composite and primitive APIs may coexist; redundancy
or a preference for single-responsibility style alone must not produce pass=false.
Examples are illustrative; do not assess task correctness by comparing them with sampled rows, and do not require examples to enumerate the real query result.
Also note in the "issue" field if you see unnecessary endpoints that no user task requires, but do NOT fail a group solely for having a few extra endpoints — only fail it for MISSING task-required capabilities or the errors above. Never fail a group solely for having extra endpoints that are correctly designed."""


# ---------- 修复（SpecFixer） ----------
SPEC_FIX_SYSTEM_PROMPT = """You are an API spec fixer. Given one resource group's current endpoints and the specific problems found by rule validation and review, fix ONLY that group. Keep correct distinct capabilities unchanged. You may remove a redundant endpoint ONLY if another group's full specification already covers all its task-required behavior. Return endpoints=[] when the entire group is unnecessary; the pipeline will remove that group and recheck global coverage. Never delete a unique task-required capability."""

SPEC_FIX_USER_PROMPT = """Fix the API endpoints for the resource group "{group_name}" of {scenario_name}.

User Tasks (context; focus on those related to this group):
{tasks_list}

Database Schema (context):
{database_schema}

Sample Data (initial state, sampled rows; empty tables are created at runtime):
{sample_data}

Current endpoints of this group:
{current_endpoints}

Problems to fix (rule validation + review issues for THIS group only):
{problems}

Fix ONLY the endpoints in this group. Keep correct endpoints unchanged. Return ONLY JSON in this format:
{{
  "group_name": "{group_name}",
  "endpoints": [
    {{
      "path": "/api/products",
      "method": "GET",
      "summary": "List all products",
      "description": "Retrieve a list of products.",
      "operation_id": "list_products",
      "tags": ["products"],
      "request_params": {{}},
      "user_scope": "all",
      "example_policy": "illustrative",
      "response": {{
        "type": "object",
        "properties": {{
          "products": {{
            "type": "array",
            "items": {{
              "type": "object",
              "properties": {{"id": {{"type": "integer"}}, "name": {{"type": "string"}}}},
              "required": ["id", "name"]
            }}
          }}
        }},
        "required": ["products"]
      }}
    }}
  ]
}}"""

# 所有角色看到相同的模块约定；LLM 只设计 API，files 由程序在汇总后生成。
# Planner 只规划名称，不承担请求/响应 schema 设计；减少无关指令干扰。
SPEC_PLANNER_SYSTEM_PROMPT += """
Return only {"groups": ["ResourceName"]}. Put the primary workflow resource FIRST.
Plan API ownership, not a group for every table mentioned: related records/logs updated
as part of one workflow can be handled by one primary group's endpoint. Add another
group only for distinct task-required operations. At least one group is required.
"""
SPEC_DESIGNER_SYSTEM_PROMPT += IMPLEMENTATION_CONTRACT_PROMPT
SPEC_REVIEWER_SYSTEM_PROMPT += IMPLEMENTATION_CONTRACT_PROMPT
SPEC_FIX_SYSTEM_PROMPT += IMPLEMENTATION_CONTRACT_PROMPT
