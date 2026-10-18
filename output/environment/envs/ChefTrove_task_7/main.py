import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AssignTaskRequest(BaseModel):
    task_id: int
    user_id: int
    shift_id: int
    assigned_by_user_id: int
    required_completion_time: str


class TaskAssignmentResponse(BaseModel):
    id: int
    task_id: int
    user_id: int
    shift_id: int
    assigned_by_user_id: int
    required_completion_time: str
    status: str
    completed_at: str | None
    created_at: str
    updated_at: str


class CompleteTaskRequest(BaseModel):
    deviation_note: str
    completed_by_user_id: int


class TaskAssignmentSummary(BaseModel):
    id: int
    status: str
    completed_at: str


class DeviationResponse(BaseModel):
    id: int
    task_assignment_id: int
    note: str
    created_by_user_id: int
    created_at: str


class CompleteTaskResponse(BaseModel):
    task_assignment: TaskAssignmentSummary
    deviation: DeviationResponse


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@app.post("/task_assignments", response_model=TaskAssignmentResponse, operation_id="assign_task_to_user")
def assign_task_to_user(req: AssignTaskRequest):
    now = _now_str()
    with engine.connect() as conn:
        # Validate foreign keys exist
        for table, col, val in [
            ("tasks", "id", req.task_id),
            ("users", "id", req.user_id),
            ("shifts", "id", req.shift_id),
            ("users", "id", req.assigned_by_user_id),
        ]:
            result = conn.execute(text(f"SELECT 1 FROM {table} WHERE {col} = :val"), {"val": val}).fetchone()
            if not result:
                raise HTTPException(status_code=400, detail=f"{table} with id {val} does not exist")

        insert_result = conn.execute(
            text(
                """INSERT INTO task_assignments 
                   (task_id, user_id, shift_id, assigned_by_user_id, required_completion_time, status, created_at, updated_at)
                   VALUES (:task_id, :user_id, :shift_id, :assigned_by_user_id, :required_completion_time, 'assigned', :created_at, :updated_at)"""
            ),
            {
                "task_id": req.task_id,
                "user_id": req.user_id,
                "shift_id": req.shift_id,
                "assigned_by_user_id": req.assigned_by_user_id,
                "required_completion_time": req.required_completion_time,
                "created_at": now,
                "updated_at": now,
            },
        )
        new_id = insert_result.lastrowid
        row = conn.execute(
            text("SELECT * FROM task_assignments WHERE id = :id"),
            {"id": new_id},
        ).fetchone()

    return TaskAssignmentResponse(
        id=row.id,
        task_id=row.task_id,
        user_id=row.user_id,
        shift_id=row.shift_id,
        assigned_by_user_id=row.assigned_by_user_id,
        required_completion_time=str(row.required_completion_time),
        status=row.status,
        completed_at=str(row.completed_at) if row.completed_at else None,
        created_at=str(row.created_at),
        updated_at=str(row.updated_at),
    )


@app.put("/task_assignments/{id}/complete", response_model=CompleteTaskResponse, operation_id="mark_task_as_completed")
def mark_task_as_completed(id: int, req: CompleteTaskRequest):
    now = _now_str()
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))

    with engine.connect() as conn:
        assignment = conn.execute(
            text("SELECT * FROM task_assignments WHERE id = :id"),
            {"id": id},
        ).fetchone()
        if not assignment:
            raise HTTPException(status_code=404, detail="Task assignment not found")

        if assignment.status == "completed":
            raise HTTPException(status_code=400, detail="Task already completed")

        # Update task assignment to completed
        conn.execute(
            text(
                """UPDATE task_assignments 
                   SET status = 'completed', completed_at = :completed_at, updated_at = :updated_at
                   WHERE id = :id"""
            ),
            {"completed_at": now, "updated_at": now, "id": id},
        )

        # Insert deviation record
        dev_insert = conn.execute(
            text(
                """INSERT INTO deviations 
                   (task_assignment_id, note, created_by_user_id, created_at, updated_at)
                   VALUES (:task_assignment_id, :note, :created_by_user_id, :created_at, :updated_at)"""
            ),
            {
                "task_assignment_id": id,
                "note": req.deviation_note,
                "created_by_user_id": req.completed_by_user_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        dev_id = dev_insert.lastrowid

        # Fetch updated assignment
        updated_assignment = conn.execute(
            text("SELECT * FROM task_assignments WHERE id = :id"),
            {"id": id},
        ).fetchone()

        # Fetch deviation
        deviation_row = conn.execute(
            text("SELECT * FROM deviations WHERE id = :id"),
            {"id": dev_id},
        ).fetchone()

    return CompleteTaskResponse(
        task_assignment=TaskAssignmentSummary(
            id=updated_assignment.id,
            status=updated_assignment.status,
            completed_at=str(updated_assignment.completed_at),
        ),
        deviation=DeviationResponse(
            id=deviation_row.id,
            task_assignment_id=deviation_row.task_assignment_id,
            note=deviation_row.note,
            created_by_user_id=deviation_row.created_by_user_id,
            created_at=str(deviation_row.created_at),
        ),
    )
