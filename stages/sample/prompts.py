"""sample 阶段（示例数据生成）的多 agent 提示词。"""

# ---------- 数据生成（DataGenerator） ----------
DATA_GENERATION_SYSTEM_PROMPT = """You are an expert database engineer. You generate the INITIAL STATE of a simulated environment's database — ALL tables at once, in ONE shot.

CRITICAL — first understand your role:
- You are NOT the agent that completes the user tasks.
- A SEPARATE AI agent will complete those tasks later, by calling APIs against the database you generate.
- You produce the "before" snapshot: the database as it looks BEFORE any task has been performed.
- Therefore:
  * objects a task CREATES/ADDS ("Add a new certification", "Create a repository", "Post a tweet") must NOT already exist in your data — they are what that agent is supposed to create from scratch;
  * objects a task READS/UPDATES/DELETES must ALREADY exist in your data — they are the starting material that agent will operate on;
  * CRITICAL: every OTHER row that the tasks or foreign keys depend on (e.g. the referenced user, group, skill, or parent rows) must STILL be generated — only the specific object a task "Adds"/"Creates" is omitted, everything else stays fully populated.

Given the FULL database schema (every table's DDL) and the user tasks, generate realistic INSERT statements for EVERY table.

You must:
- Generate an entry for EVERY table in the schema (the "tables" array must cover all of them). Never omit a whole table: the "omitted" rule above is about specific ROWS (the exact object a task creates), never about whole tables.
- Strictly follow each table's DDL (exact column names).
- Never invent or guess column names that are not in the DDL.
- Keep every foreign key reference consistent with the primary key ids you actually inserted into the parent table.
- Follow the exact output JSON format specified in the user prompt.
- Output ONLY the requested JSON (tables with insert_statements), with no reasoning, no explanation, no extra text."""

# ---------- 数据评审（DataReviewer） ----------
DATA_REVIEW_SYSTEM_PROMPT = """You are a strict reviewer for sample data generated for a web-agent environment. You check whether each table's INSERT statements are correct and high quality.

You will receive a JSON object with "scenario", "user_tasks", "db_schema", and "sample_data". Review EACH table's insert statements against the schema and return a verdict per table.

Return ONLY JSON in this format:
{{
  "results": [
    {{"table": "users", "pass": true, "issue": ""}},
    {{"table": "orders", "pass": false, "issue": "missing foreign key to users"}}
  ]
}}

The "results" array must contain exactly one entry for EVERY table in sample_data. For each entry:
- "table": the exact table name;
- "pass": true if the table's inserts are good, false if problematic;
- "issue": empty if pass, otherwise a short reason.

A table fails if:
1. its columns or values violate the schema (wrong column names / types),
2. its foreign keys / relationships are wrong or missing,
3. its values are unrealistic,
4. it contains authentication fields (password_hash, salt, session — NOT business tokens like payment/invitation/webhook token),
5. it has too few rows to support the relevant tasks,
6. it pre-populates the exact object a task CREATES/ADDS as NEW — that object must be ABSENT (a separate agent will create it at runtime); note that omitting OTHER rows the tasks/foreign keys depend on (e.g. parent rows) is a failure under rules 2/5, NOT a pass under this rule."""


# ---------- 修复（Fixer） ----------
DATA_FIX_SYSTEM_PROMPT = """You are an expert SQLite data fixer. Given a scenario, its user tasks, a db schema, the current sample data, and a list of problems, you fix the sample data.

You will receive a JSON object with "scenario", "user_tasks", "db_schema", "sample_data" and "problems". Return ONLY the tables you changed, in this format:
{{
  "tables": [
    {{"table_name": "orders", "insert_statements": ["fixed INSERT ...;", ...]}}
  ]
}}

For each problem, fix the corresponding table and return ONLY that table:
- a table reported as "missing" (schema has it but sample_data does not): ADD it — if the table is an object the task CREATES at runtime (e.g. payment/reminder/audit/notification records), return it with an EMPTY insert_statements list (zero rows, the agent creates them at runtime); otherwise return it with its insert_statements (using that table's DDL from the db_schema);
- a table reported as "not in schema" (sample_data has it but schema does not): return it with an EMPTY insert_statements list (the caller will remove it);
- review issue like "pre-populates the object a task CREATES/ADDS" / "already exists but the task must create it at runtime": REMOVE exactly those INSERT rows for the object(s) the task must create at runtime, while KEEPING all other rows (parent rows, referenced rows, and objects the task READS/UPDATES/DELETES must remain). Never delete the whole table — only the specific task-created rows;
- rule violations / review issues / SQL errors: fix that table's insert_statements and return the fixed version.

Return ONLY the tables you changed, added, or want removed. Do NOT return tables that are already correct."""


# ---------- 全库一次性生成（DataGenerator，替代逐表 map_reduce） ----------
ALL_TABLES_GENERATION_USER_PROMPT = """Generate sample data (INSERT statements) for ALL tables in the {scenario_name} environment, in ONE shot.

You generate the INITIAL STATE of the database BEFORE any task is performed. A SEPARATE agent will complete the user tasks later by calling APIs.

User Tasks (do NOT pre-complete the objects these tasks CREATE/ADD; but DO generate the parent rows and other rows the tasks and foreign keys depend on):
{tasks_list}

Database Schema (ALL tables with their DDL, including foreign key relationships):
{database_schema}

Schema Compliance Rules:
1. Use EXACT column names from each table's DDL; never invent, shorten, or rename columns.
2. Each INSERT must explicitly list column names AND end with a semicolon (;): INSERT INTO table_name (col1, col2) VALUES (val1, val2);
3. The number of columns MUST equal the number of values.
4. Use NULL for null values, 0/1 for booleans, ISO 8601 (YYYY-MM-DD HH:MM:SS) for timestamps.
5. For the primary key id, use sequential integers starting from 1 (1, 2, 3, ...).
6. CRITICAL — foreign keys must reference primary key ids that you ACTUALLY inserted into the parent table. For example, if you insert users with id 1,2,3, then orders must reference user_id 1/2/3 (never 10). Keep every foreign key reference consistent with the parent rows you generated.
7. DO NOT include authentication fields (password_hash, salt, session). IMPORTANT: "token" is NOT an authentication field here — business tokens (payment token, invitation token, webhook secret) are legitimate data values; keep a token value whenever a task needs it.
8. Generate only as much data as the task needs: aim for 2-5 rows per table by default, but if the task requires more data to be completable, generate more — just avoid redundant rows. Generate MORE rows only when a task explicitly requires a large collection (e.g. "top 250 movies", "list 100 products"), then generate exactly enough to satisfy it.
9. Use realistic, diverse values (but do NOT repeat near-identical rows).
10. Every INSERT statement must be COMPLETE (never truncated): write out all columns and values in full, and end each statement with a semicolon (;).
11. Keep the TOTAL output compact: aim for under 60 INSERT statements total. If the task genuinely needs more data to be completable, generating more is acceptable — just avoid unnecessary/redundant rows.

Output ONLY JSON (no reasoning, no explanation, no extra text):
{{
  "tables": [
    {{"table_name": "users", "insert_statements": ["INSERT INTO users (...) VALUES (...);", ...]}},
    {{"table_name": "orders", "insert_statements": ["INSERT INTO orders (...) VALUES (...);", ...]}}
  ]
}}

The "tables" array must contain an entry for EVERY table in the schema, each with its insert_statements."""


