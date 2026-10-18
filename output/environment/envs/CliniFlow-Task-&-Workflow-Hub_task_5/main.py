import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CliniFlow Task & Workflow Hub")


class RoleOut(BaseModel):
    id: int
    name: str


class EscalateRequest(BaseModel):
    to_role_id: int
    priority: str
    note: str


class EscalateResponse(BaseModel):
    task_id: int
    escalation_id: int
    status: str


@app.get("/roles", response_model=List[RoleOut], operation_id="find_roles")
def find_roles(name: Optional[str] = Query(None, description="Role name")):
    with engine.connect() as conn:
        if name:
            result = conn.execute(
                text("SELECT id, name FROM roles WHERE name LIKE :name"),
                {"name": f"%{name}%"},
            )
        else:
            result = conn.execute(text("SELECT id, name FROM roles"))
        rows = result.fetchall()
    return [RoleOut(id=row[0], name=row[1]) for row in rows]


@app.post("/tasks/{task_id}/escalate", response_model=EscalateResponse, operation_id="escalate_task")
def escalate_task(task_id: int, body: EscalateRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user")
    try:
        escalated_by_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authenticated user")

    # Validate priority against allowed values from schema
    allowed_priorities = {"Low", "Medium", "High", "Critical"}
    if body.priority not in allowed_priorities:
        raise HTTPException(status_code=422, detail=f"Priority must be one of {sorted(allowed_priorities)}")

    with engine.begin() as conn:
        # Check task exists and get its details
        task_row = conn.execute(
            text("SELECT id, title, status, assignee_user_id FROM tasks WHERE id = :tid"),
            {"tid": task_id},
        ).fetchone()
        if not task_row:
            raise HTTPException(status_code=404, detail="Task not found")

        task_title = task_row[1]
        task_status = task_row[2]
        from_user_id = task_row[3]

        # Check target role exists
        role_row = conn.execute(
            text("SELECT id, name FROM roles WHERE id = :rid"),
            {"rid": body.to_role_id},
        ).fetchone()
        if not role_row:
            raise HTTPException(status_code=404, detail="Target role not found")

        # Insert escalation record
        insert_result = conn.execute(
            text("""
                INSERT INTO task_escalations (task_id, escalated_by_user_id, from_user_id, to_role_id, priority, note, reason, status)
                VALUES (:task_id, :escalated_by, :from_user, :to_role, :priority, :note, :reason, 'pending')
            """),
            {
                "task_id": task_id,
                "escalated_by": escalated_by_user_id,
                "from_user": from_user_id,
                "to_role": body.to_role_id,
                "priority": body.priority,
                "note": body.note,
                "reason": f"Escalation of task '{task_title}'",
            },
        )
        escalation_id = insert_result.lastrowid

        # Update task status to 'escalated' and set assignee_role_id to the target role
        conn.execute(
            text("""
                UPDATE tasks
                SET status = 'escalated', assignee_role_id = :role_id, updated_at = CURRENT_TIMESTAMP
                WHERE id = :tid
            """),
            {"role_id": body.to_role_id, "tid": task_id},
        )

        # Log activity
        conn.execute(
            text("""
                INSERT INTO task_activity_log (task_id, user_id, action, details)
                VALUES (:task_id, :user_id, 'escalated', :details)
            """),
            {
                "task_id": task_id,
                "user_id": escalated_by_user_id,
                "details": f"Task escalated to role ID {body.to_role_id} with priority {body.priority}. Note: {body.note}",
            },
        )

    return EscalateResponse(task_id=task_id, escalation_id=escalation_id, status="escalated")
