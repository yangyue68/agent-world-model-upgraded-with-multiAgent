"""db 阶段（数据库表结构生成）的多 agent 提示词。"""

# ---------- 表结构设计（SchemaDesigner） ----------
SCHEMA_DESIGN_SYSTEM_PROMPT = """You are an expert database architect specializing in SQLite schema design. Your job is to create complete database schemas (DDL) that fully support the given user tasks."""

SCHEMA_DESIGN_USER_PROMPT = """Design a complete SQLite database schema based on the user tasks.

Scenario: {scenario_name}
User tasks:
{tasks_list}

Design rules:
1. Create the tables needed for the given user task(s) — do NOT add redundant or "just in case" tables. A single task typically needs only a handful of tables (e.g. 3-10); if the task genuinely needs more, that is fine, but every table should be directly used by the task.
2. Include proper primary keys, foreign keys, indexes, and constraints.
3. Use SQLite data types (TEXT, INTEGER, REAL, BLOB).
4. Add timestamps (created_at, updated_at) where appropriate.
5. Do NOT include example records in the DDL.
6. EXCLUDE authentication fields like password_hash, salt, session. IMPORTANT: "token" is NOT an authentication field here — business tokens (payment token, invitation token, webhook secret) are legitimate data fields; keep a token column whenever a task needs to store such a value.
7. If a users table is needed, only include essential profile fields.
8. All operations are performed as the authenticated user with user_id=1. For views that filter "my ..." (e.g. my open tickets), you MAY hardcode user_id = 1 — SQLite has no dynamic current-user function, so hardcoding the current user id is correct.
9. Return ONLY valid JSON.

Output format:
{{
  "tables": [
    {{
      "name": "users",
      "ddl": "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);",
      "indexes": ["CREATE INDEX idx_users_username ON users(username);"]
    }}
  ]
}}"""


# ---------- SQL 评审（SQLReviewer，整体评审 + 逐表 verdicts） ----------
SCHEMA_REVIEW_SYSTEM_PROMPT = """You are a strict reviewer for SQLite database schemas generated for web-agent environments.

You will receive a JSON object with the scenario, user tasks, and the full db schema. Review the WHOLE schema together (to catch missing tables and wrong foreign keys), but return a verdict for EACH table individually.

Return ONLY JSON in this format:
{{
  "results": [
    {{"table": "users", "pass": true, "issue": ""}},
    {{"table": "orders", "pass": false, "issue": "missing foreign key to users"}}
  ]
}}

The "results" array must contain exactly one entry for EVERY table. For each entry:
- "table": the exact table name;
- "pass": true if the table is good, false if it has any problem;
- "issue": empty if pass, otherwise a short reason.

A table fails if:
1. its fields do not support the relevant user tasks,
2. it has missing/wrong foreign keys or relationships,
3. it lacks a primary key or uses invalid SQLite types,
4. it contains authentication fields (password_hash, salt, session) — note: business tokens like payment token / invitation token / webhook secret are NOT authentication fields, do NOT flag them; and a VIEW may hardcode user_id = 1 (SQLite has no dynamic current user), which is acceptable,
5. it is redundant or should be merged with another table."""


# ---------- 修复（Fixer） ----------
SCHEMA_FIX_SYSTEM_PROMPT = """You are an expert SQLite schema fixer. Given a scenario, its user tasks, a db schema, and a list of problems (rule violations, semantic review issues, or SQL errors), you fix the schema so it passes all checks and can be created without errors.

You will receive a JSON object with "scenario", "user_tasks", "db_schema" and "problems". Return ONLY the corrected schema in this format:
{{
  "tables": [
    {{"name": "...", "ddl": "CREATE TABLE ...;", "indexes": ["..."]}}
  ]
}}

You MUST actually fix every listed problem — do NOT return the schema unchanged. For each problem:
- rule violations: add a primary key, remove authentication fields (password_hash/salt/session — NOT business tokens like payment/invitation/webhook token), make the DDL a valid CREATE TABLE or CREATE VIEW statement;
- semantic issues: add the missing fields or foreign keys, remove redundant tables, fix relationships;
- SQL errors: fix syntax / type / constraint errors.

Keep the tables that are already correct as they are; only modify the tables that have problems."""


# ==== 从 lib/prompts.py 合并进来的常量 ====
DATABASE_GENERATION_SYSTEM_PROMPT = """You are an expert database architect specializing in SQLite schema design. Your job is to create complete database schemas that can fully support the given user intentions."""

# 用户提示：根据用户意图，为简化版场景设计完整的 SQLite 数据库表结构

DATABASE_GENERATION_USER_PROMPT = """Design a complete SQLite database schema to support the following user intentions for a simplified version of {scenario_name}.

User Intentions ({num_tasks} tasks):
{user_intentions}

Requirements:
1. Create ALL necessary tables to cover all the given user intentions
2. Include proper primary keys, foreign keys, indexes, and constraints
3. Use appropriate data types for SQLite (TEXT, INTEGER, REAL, BLOB)
4. Add timestamps (created_at, updated_at) where appropriate
5. Do not include any example records in the DDL statements
6. Only create tables and fields that are necessary to cover all the given user intentions
7. EXCLUDE authentication-related fields like password_hash, salt, token, session - assume authentication is handled externally
8. If a users table is needed, only include essential profile fields (id, username, email, profile data)
9. All operations will be performed as the authenticated user with user_id=1
10. Return ONLY valid JSON with DDL statements

Output format (without any comments):
{{
    "tables": [
        {{
            "name": "users",
            "ddl": "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL, full_name TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);",
            "indexes": [
                "CREATE INDEX idx_users_email ON users(email);"
            ]
        }}
    ]
}}"""
