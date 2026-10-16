"""task 阶段（任务生成）的多 agent 提示词。"""

# ---------- 修复（Fixer） ----------
TASK_FIXER_SYSTEM_PROMPT = """You are an expert in web automation and user task analysis. Given a scenario, its task angles, the current user tasks (grouped by angle), and a list of problems (rule violations or semantic review issues), you fix the tasks so they pass all checks.

You will receive a JSON object with "scenario", "description", "angle_tasks" (the current tasks grouped by angle), and "problems". Return ONLY JSON in this format:
{{
  "angle_tasks": [
    {{"angle": "angle 1", "tasks": ["fixed task 1", "fixed task 2"]}},
    {{"angle": "angle 2", "tasks": ["fixed task 3"]}}
  ]
}}

Fix ALL the listed problems while keeping the good tasks and their angles unchanged:
- rule violations: remove authentication/login/register keywords, remove GUI/download keywords, fix task length;
- semantic issues: make tasks specific/actionable/relevant to the scenario, add concrete details (product name / location / amount / target), avoid login/register/GUI actions, and reduce unreasonably large data thresholds (e.g. "at least 1,000 reviews" → "at least 5 reviews", "90-day history" → "7-day history").
"""


# ---------- 生成（Generator，MA 版：删除"单句"要求，允许一段话） ----------
TASK_GENERATION_SYSTEM_PROMPT_MA = """You are an expert in web automation and user task analysis. Your job is to generate realistic, diverse user tasks for scenarios."""

TASK_GENERATION_USER_PROMPT_MA = """Generate {num_tasks} realistic and diverse user tasks for the following scenario.

Scenario: {scenario_name}
Description: {scenario_description}

Requirements:
1. Tasks should be specific and actionable
2. Cover different user scenarios (beginner to advanced)
3. Include both common and less common use cases
4. Tasks should be practical and realistic
5. Each task should be a complete, self-contained instruction; it may be one sentence or a short paragraph, but must include all necessary details (e.g., product name, location, amount, target)
6. If the scenario typically requires authentication, EXCLUDE any authentication, login, logout, or user registration tasks - assume the user is already logged in
7. If the scenario typically requires authentication, all tasks should be from the perspective of the current authenticated user
8. Return ONLY a JSON object with a single "tasks" key whose value is an array of task strings, no additional text or annotations
9. The scenario is a simplified version providing API endpoints for task completion. Avoid generating tasks that require direct user interaction such as download a file, open a page, etc.
10. Avoid tasks that demand an unrealistically large volume of data to complete (e.g. "at least 1,000 reviews", "at least 100 attendees", "90-day history"). Keep filtering thresholds modest (e.g. "top 3", "at least 5 reviews", "last 7 days") so the downstream data-generation stage and the future agent can satisfy them without generating huge datasets.

Examples:
- For scenario "Amazon", a task could be "Search for 'laptop' and add the cheapest result to the cart"
- For scenario "Reddit", a task could be "Get the number of posts in the 'r/python' subreddit"
- For scenario "Expedia", a task could be "Book a flight from New York to London on October 1st"
- For scenario "Twitter/X", a task could be "Post a tweet with a photo, add alt text "Sunset over the city", include the hashtag #Photography, and mention @Adobe."
- For scenario "LinkedIn", a task could be "Update my profile headline to 'Senior Data Analyst | SQL, Python, Tableau' and rewrite the About section to a concise 3-paragraph summary highlighting business impact."
- For scenario "Facebook", a task could be "Share your latest post to your friend list."
- For scenario "Google Maps", a task could be "Get 5 most popular restaurants in San Francisco."

Output format:
{{
    "tasks": [
        "Task 1 description",
        "Task 2 description",
        ...
    ]
}}"""


# ---------- 评审（Reviewer，语义层） ----------
TASK_REVIEWER_SYSTEM_PROMPT = """You are a strict reviewer for user tasks generated for web-agent environments.

You will receive a JSON object like:
{
  "scenario": "scenario name",
  "description": "scenario description",
  "angle_tasks": [
    {"angle": "angle 1", "tasks": ["task 1", "task 2", ...]},
    {"angle": "angle 2", "tasks": ["task 3", "task 4", ...]}
  ]
}

Review EACH task individually and return ONLY JSON in this format:
{
  "results": [
    {"angle": "angle 1", "task": "task 1", "pass": true, "issue": ""},
    {"angle": "angle 1", "task": "task 2", "pass": false, "issue": "why this task fails"}
  ]
}

The "results" array must contain exactly one entry for EVERY task. For each entry:
- "angle": the angle this task belongs to (copied verbatim from the input);
- "task": the exact task text (copied verbatim);
- "pass": true if the task is good, false if it violates any rule below;
- "issue": empty string if pass=true, otherwise a short reason.

A task fails (pass=false) if it:
1. is irrelevant to the scenario (not something a user would do in this scenario),
2. is vague or missing concrete details (no product name / location / amount / target / recipient),
3. is itself about login / register / logout / credential management (note: accessing the current user's own data — ride history, receipts, settings — is allowed, since the user is assumed to be already logged in),
4. requires GUI interaction, file download, or opening a page,
5. is not a clear, actionable instruction (it may be one sentence or a short paragraph),
6. demands an unrealistically large volume of data to complete (e.g. thresholds like "at least 1,000 reviews", "at least 100 attendees", "90-day history") — such tasks force the downstream data stage to generate huge datasets and are not good tasks."""
