import os
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CliniFlow Task & Workflow Hub")


class LookupResponse(BaseModel):
    id: int


class TaskItem(BaseModel):
    id: int
    title: str
    description: str
    assignee_full_name: str
    due_at: str


@app.get("/lookup/task-type", response_model=LookupResponse, operation_id="lookup_task_type_id")
def lookup_task_type(name: str = Query(..., description="Name of the task type to look up")):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM task_types WHERE name = :name"),
            {"name": name}
        ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Task type not found")
    return LookupResponse(id=result[0])


@app.get("/lookup/role", response_model=LookupResponse, operation_id="lookup_role_id")
def lookup_role(name: str = Query(..., description="Name of the role to look up")):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM roles WHERE name = :name"),
            {"name": name}
        ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Role not found")
    return LookupResponse(id=result[0])


@app.get("/lookup/department", response_model=LookupResponse, operation_id="lookup_department_id")
def lookup_department(name: str = Query(..., description="Name of the department to look up")):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM departments WHERE name = :name"),
            {"name": name}
        ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Department not found")
    return LookupResponse(id=result[0])


@app.get(
    "/tasks/pre-visit-checklist/medical-assistants/cardiology/due-next-24-hours",
    response_model=list[TaskItem],
    operation_id="find_pre_visit_checklist_tasks"
)
def find_pre_visit_checklist_tasks():
    # Get IDs for filtering
    with engine.connect() as conn:
        task_type_result = conn.execute(
            text("SELECT id FROM task_types WHERE name = 'Pre-Visit Checklist'")
        ).fetchone()
        if not task_type_result:
            raise HTTPException(status_code=500, detail="Task type 'Pre-Visit Checklist' not found in database")
        task_type_id = task_type_result[0]

        role_result = conn.execute(
            text("SELECT id FROM roles WHERE name = 'Medical Assistant'")
        ).fetchone()
        if not role_result:
            raise HTTPException(status_code=500, detail="Role 'Medical Assistant' not found in database")
        role_id = role_result[0]

        dept_result = conn.execute(
            text("SELECT id FROM departments WHERE name = 'Cardiology'")
        ).fetchone()
        if not dept_result:
            raise HTTPException(status_code=500, detail="Department 'Cardiology' not found in database")
        dept_id = dept_result[0]

        status_result = conn.execute(
            text("SELECT id FROM task_statuses WHERE name = 'Active'")
        ).fetchone()
        if not status_result:
            raise HTTPException(status_code=500, detail="Status 'Active' not found in database")
        active_status_id = status_result[0]

    # Calculate time window: now to now + 24 hours
    now = datetime.utcnow()
    next_24_hours = now + timedelta(hours=24)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    next_24_hours_str = next_24_hours.strftime("%Y-%m-%d %H:%M:%S")

    # Query tasks
    query = """
    SELECT 
        t.id,
        t.title,
        t.description,
        u.full_name AS assignee_full_name,
        t.due_at
    FROM tasks t
    JOIN users u ON t.assignee_id = u.id
    WHERE t.task_type_id = :task_type_id
      AND t.status_id = :status_id
      AND t.department_id = :dept_id
      AND u.role_id = :role_id
      AND u.is_active = 1
      AND t.due_at >= :now
      AND t.due_at <= :next_24_hours
    ORDER BY t.due_at ASC
    """

    with engine.connect() as conn:
        results = conn.execute(
            text(query),
            {
                "task_type_id": task_type_id,
                "status_id": active_status_id,
                "dept_id": dept_id,
                "role_id": role_id,
                "now": now_str,
                "next_24_hours": next_24_hours_str,
            }
        ).fetchall()

    items = []
    for row in results:
        items.append(TaskItem(
            id=row[0],
            title=row[1],
            description=row[2] if row[2] is not None else "",
            assignee_full_name=row[3],
            due_at=row[4].strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(row[4], datetime) else str(row[4])
        ))

    return items
