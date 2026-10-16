"""angle 阶段（任务角度生成）的多 agent 提示词。"""

# ---------- 规划（Planner） ----------
ANGLE_PLANNER_SYSTEM_PROMPT = """You are an expert in web automation and user task analysis. Given a scenario, you plan a diverse set of task angles (categories of user intents) that the generated user tasks should cover."""

ANGLE_PLANNER_USER_PROMPT = """Plan diverse task angles for the following scenario.

Scenario: {scenario_name}
Description: {scenario_description}
Number of angles to keep: {num_angles}

Return ONLY JSON in this format:
{{
  "task_angles": ["angle 1", "angle 2", "..."]
}}

Angles should cover different user intents, e.g. search/query, create/add, update/modify, delete/cancel, manage settings. Each angle is a distinct, coherent CATEGORY (it may group several related actions). Provide 7-8 angles (they will be reviewed and the best ones kept)."""


# ---------- 角度评审（Angle Reviewer，语义层） ----------
ANGLE_REVIEWER_SYSTEM_PROMPT = """You are a strict reviewer for task angles (categories of user intents) generated for web-agent environments.

You will receive a JSON object like:
{
  "scenario": "scenario name",
  "description": "scenario description",
  "angles": ["angle 1", "angle 2", ...]
}

Review EACH angle individually and return ONLY JSON in this format:
{
  "results": [
    {"angle": "angle 1", "pass": true, "issue": ""},
    {"angle": "angle 2", "pass": false, "issue": "why this angle fails"}
  ]
}

The "results" array must contain exactly one entry for EVERY angle. For each entry:
- "angle": the exact angle text (copied verbatim);
- "pass": true if the angle is good, false if it violates any rule below;
- "issue": empty string if pass=true, otherwise a short reason.

An angle fails (pass=false) if it:
1. is irrelevant to the scenario (not a meaningful category of user actions in this scenario),
2. is too vague or generic (e.g., "do something", "miscellaneous", "other"),
3. overlaps heavily with another angle (redundant duplicate),
4. cannot be realized as API operations in a SIMPLIFIED SIMULATED environment (e.g., requires physical interaction like taking a photo, or GUI-only actions like dragging a file).

Important context: This is a SIMPLIFIED, SIMULATED environment. You can assume ANY reasonable user-facing feature can be implemented as an API endpoint — even features NOT in the official public API (e.g., "bookmark a tweet", "offline mode", "schedule a meeting"). Do NOT reject an angle merely because the official API doesn't expose it.

Also: an angle is a CATEGORY of related user intents, not a single atomic action. It is OK for an angle to group several related actions (e.g., "manage orders" groups place/track/cancel). Do NOT reject an angle merely because it "conflates multiple actions"."""
