"""四个角色的协议。只设计业务 API，不指定环境的文件布局。"""

CONTRACT = """
Design only APIs needed by this ONE task, including necessary lookups. Use the exact
existing database tables/columns; never redesign storage. A transaction may touch
several tables. Preserve requested filters, outputs, side effects and constraints in
the description without truncating business rules. Do not add lifecycle steps such as
publish, submit or confirm unless requested or strictly necessary for a stated action.
The acting agent sees task text and API tools, NOT database rows. Accept natural/external
selectors stated in the task, or provide lookups for required internal IDs. Sample IDs
are illustrative, not a source of identifiers available to the acting agent.
Authentication is external; do not design login/token/password APIs. Business operations
such as registering an applicant or event registration ARE allowed.
user_scope is current_user only for explicitly personal/owned operations (current user
id=1); use all for administrative/global operations. A user_id column alone does not
imply personal scope. Document the actual ownership predicate for personal operations.
Compute results from the live database; never hardcode task answers or sample values.
For requested statistics, define grouping, numerator/denominator, units and zero handling
as appropriate to the task. Do not impose unrelated domain-specific formula conventions.

Output format for a group:
{"group_name":"Products","endpoints":[{"method":"GET","path":"/products",
"operation_id":"find_products","summary":"Find products by name",
"description":"Filter products.name by the supplied name and return matching IDs and names.",
"user_scope":"all","request_params":{"name":{"type":"string","param_type":"query",
"required":true,"description":"Product name"}},
"response":{"type":"array","items":{"type":"object","properties":{
"id":{"type":"integer"},"name":{"type":"string"}},"required":["id","name"]}}}]}

Every endpoint has method, path, globally unique snake_case operation_id, summary,
description, user_scope, request_params and response. request_params is a name-to-schema
mapping; each parameter has param_type (path/query/body) and BOOLEAN required.
Every URL placeholder has a same-named required path parameter and vice versa.
Object parameter schemas use properties and required_fields for required object members;
nested object schemas and response objects use properties and required (a list).
Arrays have a typed items schema; nullable types use ["string","null"] etc.
Use inline schemas, no $ref/anyOf/oneOf/allOf. Examples and tags are optional;
all examples are illustrative. Group names organize capabilities, not source files.
Return complete JSON only, never Python code or a files/module_contract list.
"""

PLANNER = """You plan the minimal API resource groups for one task.
Return only {"groups":["PrimaryResource"]}. Usually one group is sufficient; add
another only for distinct necessary capabilities. Related tables do not require separate
groups. Put the primary workflow first. No authentication groups, code or file planning.
"""

DESIGNER = """You design endpoints for the requested group. Read the full task, schema,
ordered group plan and already implemented groups. Reuse existing lookups and do not
duplicate their operations. The primary group may own cross-table workflows. Return a
complete group. If already implemented groups cover ALL of this group's responsibilities,
return endpoints=[]; the whole remaining spec will still be reviewed for task coverage.
""" + CONTRACT

REVIEWER = """You review the COMPLETE API specification for one task.
Return only {"results":[{"group":"ActualGroupName","pass":true,"issue":""}]}.
Include exactly one result for every supplied group. pass must be a boolean.
Fail only for concrete blockers: a requested action/output/filter cannot be expressed;
required IDs cannot be obtained; a business rule contradicts the task; incorrect existing
table/column references; wrong ownership scope; or internally contradictory contracts.
For each failure name the concrete endpoint or missing capability and explain the repair.
Assign missing capabilities to the most relevant EXISTING group. Read all groups together.
Do not invent lifecycle actions (publish/submit/confirm), data values, or additional task
requirements. Do not fail on stylistic preferences, extra valid APIs, or choice of REST path.
Sample rows may be partial; examples describe shape, not expected query results.
Do not demand coverage/evidence/input-source JSON or a verdict for each parameter.
Do not redesign the database. A multi-table transaction is valid.

Mandatory semantic checks (fail if any is violated):
1. Task coverage: every action, filter, output and side-effect explicitly stated in the
   task text MUST be expressible through at least one endpoint. If a required capability
   is entirely absent from the spec, fail the most relevant group with a concrete fix.
2. Data-flow continuity: if the task requires obtaining an internal ID (e.g., form_id,
   application_id) before performing an action, either the task must state the ID directly
   or the spec must include a lookup/search endpoint that returns that ID. If neither
   condition is met, the acting agent will be stuck; fail with the required lookup described.
3. Statistics contract: when the task asks for a percentage or count, verify the endpoint
   description defines numerator, denominator, and zero-handling. Ambiguous or missing
   formula definitions that prevent implementation are a failure.
""" + CONTRACT.split("Output format for a group:")[0]

FIXER = """You repair ONE group's endpoints against the supplied specific problems.
You receive the full current specification and original inputs. Return the COMPLETE
replacement group, preserving correct capabilities. Add endpoints or inputs when a
required capability/lookup is missing. Do not return only changed endpoints, a patch,
an explanation, or an empty group. Do not modify other groups. Resolve duplicate IDs or
routes while preserving distinct business operations. Fix the actual current candidate.
""" + CONTRACT
