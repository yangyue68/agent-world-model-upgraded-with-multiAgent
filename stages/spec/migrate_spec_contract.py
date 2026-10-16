"""把试运行产生的旧 spec 迁移到递归 response 契约。

该脚本只修改 spec JSONL，不调用模型，也不修改数据库。它适用于当前两个试运行任务；
以后正式重生成时，generate.py 会直接产出同样的新契约。
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "output/spec/gen_spec_ma.jsonl"


def scalar(kind, description, example, *, nullable=False):
    schema = {"type": [kind, "null"] if nullable else kind,
              "description": description, "example": example}
    return schema


def obj(properties, required, description=None, example=None):
    result = {"type": "object", "properties": properties,
              "required": required, "additionalProperties": False}
    if description:
        result["description"] = description
    if example is not None:
        result["example"] = example
    return result


def array(items, description, example=None):
    result = {"type": "array", "items": items, "description": description}
    if example is not None:
        result["example"] = example
    return result


def read_rows(db_path, task_id):
    conn = sqlite3.connect((ROOT / db_path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if task_id == "task_1":
            sql = """SELECT u.full_name, u.email
                     FROM applicants a JOIN users u ON u.id=a.user_id
                     JOIN camps c ON c.id=a.camp_id
                     WHERE c.name='Robotics Camp' AND c.term='Summer' AND c.year=2025
                       AND a.application_status='Incomplete'
                       AND NOT EXISTS (SELECT 1 FROM documents d
                                       WHERE d.applicant_id=a.id
                                         AND d.document_type='Recommendation Letter')
                     ORDER BY u.full_name"""
            return [dict(row) for row in conn.execute(sql)]
        sql = """SELECT u.username, u.full_name, u.email,
                         co.id committee_id, co.name committee_name,
                         st.id stage_id, st.name stage_name, st.stage_order,
                         COUNT(DISTINCT a.id) total_assigned,
                         COUNT(DISTINCT CASE WHEN a.final_decision IN ('admit','waitlist','deny') THEN a.id END) decided_total,
                         COUNT(DISTINCT CASE WHEN a.final_decision IS NULL THEN a.id END) undecided_total,
                         COUNT(DISTINCT CASE WHEN a.final_decision='admit' THEN a.id END) admit,
                         COUNT(DISTINCT CASE WHEN a.final_decision='waitlist' THEN a.id END) waitlist,
                         COUNT(DISTINCT CASE WHEN a.final_decision='deny' THEN a.id END) deny
                  FROM review_assignments ra
                  JOIN applications a ON a.id=ra.application_id
                  JOIN program_terms pt ON pt.id=a.program_term_id
                  JOIN programs p ON p.id=pt.program_id
                  JOIN terms t ON t.id=pt.term_id
                  JOIN users u ON u.id=ra.reviewer_id
                  JOIN review_stages st ON st.id=ra.stage_id
                  JOIN review_committees co ON co.id=st.committee_id
                  WHERE t.name='Fall 2025' AND p.degree_type='MBA'
                  GROUP BY u.id, st.id
                  ORDER BY u.username, st.stage_order"""
        return [dict(row) for row in conn.execute(sql)]
    finally:
        conn.close()


def repair_task_1(endpoint, rows):
    item = obj({
        "full_name": scalar("string", "users.full_name of the applicant.", "Alice Johnson"),
        "email": scalar("string", "users.email of the applicant.", "alice.johnson@example.com"),
    }, ["full_name", "email"])
    endpoint["user_scope"] = "all"
    endpoint["example_policy"] = "illustrative"
    endpoint["description"] += " Examples below illustrate the response shape only; results are always computed from the live database."
    endpoint["response"] = obj({
        "results": array(item, "Matching applicant contact information.", [
            {"full_name": "Alice Johnson", "email": "alice.johnson@example.com"},
            {"full_name": "Brian Lee", "email": "brian.lee@example.com"},
        ])
    }, ["results"])


def repair_task_3(endpoint, rows):
    def pct(row, key):
        return round(row[key] * 100 / row["total_assigned"], 2) if row["total_assigned"] else None

    item_properties = {
        "reviewer": obj({
            "id": scalar("integer", "users.id.", 1),
            "username": scalar("string", "users.username.", "jchen"),
            "full_name": scalar("string", "users.full_name.", "Jennifer Chen", nullable=True),
            "email": scalar("string", "users.email.", "jennifer.chen@example.edu", nullable=True),
        }, ["id", "username", "full_name", "email"]),
        "committee": obj({
            "id": scalar("integer", "review_committees.id.", 1),
            "name": scalar("string", "review_committees.name.", "MBA Admissions Committee"),
        }, ["id", "name"]),
        "stage": obj({
            "id": scalar("integer", "review_stages.id.", 1),
            "name": scalar("string", "review_stages.name.", "Initial Screening"),
            "stage_order": scalar("integer", "review_stages.stage_order.", 1),
        }, ["id", "name", "stage_order"]),
        "total_assigned": scalar("integer", "Distinct applications assigned to this reviewer and stage.", 3),
        "decided_total": scalar("integer", "Assigned applications with final_decision admit, waitlist, or deny.", 2),
        "undecided_total": scalar("integer", "Assigned applications whose final_decision is null.", 1),
        "decision_counts": obj({
            "admit": scalar("integer", "Count with final_decision='admit'.", 1),
            "waitlist": scalar("integer", "Count with final_decision='waitlist'.", 1),
            "deny": scalar("integer", "Count with final_decision='deny'.", 0),
        }, ["admit", "waitlist", "deny"]),
        "decision_percentages": obj({
            "admit": scalar("number", "admit count / total_assigned * 100, rounded to two decimals.", 33.33),
            "waitlist": scalar("number", "waitlist count / total_assigned * 100, rounded to two decimals.", 33.33),
            "deny": scalar("number", "deny count / total_assigned * 100, rounded to two decimals.", 0.0),
        }, ["admit", "waitlist", "deny"], "Each decision percentage uses total_assigned as denominator; it is not conditioned on decided applications."),
        "decision_coverage_percent": scalar("number", "decided_total / total_assigned * 100, rounded to two decimals; null when total_assigned is zero.", 66.67, nullable=True),
    }
    first = rows[0]
    example_item = {
        "reviewer": {"id": 1, "username": first["username"], "full_name": first["full_name"], "email": first["email"]},
        "committee": {"id": first["committee_id"], "name": first["committee_name"]},
        "stage": {"id": first["stage_id"], "name": first["stage_name"], "stage_order": first["stage_order"]},
        "total_assigned": first["total_assigned"], "decided_total": first["decided_total"],
        "undecided_total": first["undecided_total"],
        "decision_counts": {"admit": first["admit"], "waitlist": first["waitlist"], "deny": first["deny"]},
        "decision_percentages": {"admit": pct(first, "admit"), "waitlist": pct(first, "waitlist"), "deny": pct(first, "deny")},
        "decision_coverage_percent": round(first["decided_total"] * 100 / first["total_assigned"], 2) if first["total_assigned"] else None,
    }
    endpoint["user_scope"] = "all"
    endpoint["example_policy"] = "illustrative"
    endpoint["description"] = (
        "Returns an aggregated reviewer workload report for one program_term. The caller must provide term_name and at least one program filter: program_id, program_name, or program_degree_type. "
        "The endpoint joins terms, programs, program_terms, applications, review_assignments, review_stages, review_committees, and users. For each reviewer and committee stage, it counts distinct assigned applications. "
        "decision_counts count final_decision values admit, waitlist, and deny. Every decision_percentages value uses total_assigned as its denominator, is multiplied by 100, and rounded to two decimals; decision_coverage_percent is decided_total / total_assigned. "
        "All percentages are null when their denominator is zero. Return 400 if term_name is missing or no program filter is provided, 404 if no matching term, program, or program_term exists, and 409 if the filters match multiple program_terms. This is a read-only administrative query over all reviewers, not a current-user query. Examples illustrate shape only and are not expected task answers."
    )
    endpoint["response"] = obj({
        "term": obj({"id": scalar("integer", "Matched terms.id.", 1), "name": scalar("string", "Matched terms.name.", "Fall 2025")}, ["id", "name"]),
        "program": obj({"id": scalar("integer", "Matched programs.id.", 1), "name": scalar("string", "Matched programs.name.", "Master of Business Administration"), "degree_type": scalar("string", "Matched programs.degree_type.", "MBA")}, ["id", "name", "degree_type"]),
        "program_term_id": scalar("integer", "Resolved program_terms.id.", 1),
        "filters": obj({"term_name": scalar("string", "Applied term_name.", "Fall 2025"), "program_id": scalar("integer", "Applied program_id.", None, nullable=True), "program_name": scalar("string", "Applied program_name.", None, nullable=True), "program_degree_type": scalar("string", "Applied program_degree_type.", "MBA", nullable=True)}, ["term_name", "program_id", "program_name", "program_degree_type"]),
        "items": array(obj(item_properties, list(item_properties)), "One row per reviewer and committee stage with at least one assignment.", [example_item]),
    }, ["term", "program", "program_term_id", "filters", "items"])


def migrate():
    records = [json.loads(line) for line in INPUT.read_text(encoding="utf-8").splitlines() if line.strip()]
    db_rows = {}
    for line in (ROOT / "output/db/gen_db_ma.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            db_rows[(row["scenario"], row["task_id"])] = row
    for record in records:
        rows = read_rows(db_rows[(record["scenario"], record["task_id"])] ["db_path"], record["task_id"])
        endpoint = record["api_spec"]["api_groups"][0]["endpoints"][0]
        if record["task_id"] == "task_1":
            repair_task_1(endpoint, rows)
        elif record["task_id"] == "task_3":
            repair_task_3(endpoint, rows)
        else:
            raise ValueError(f"当前迁移脚本不支持 {record['task_id']}")
    fd, tmp = tempfile.mkstemp(prefix="gen_spec_ma.", suffix=".jsonl", dir=INPUT.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(tmp, INPUT)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


if __name__ == "__main__":
    migrate()
