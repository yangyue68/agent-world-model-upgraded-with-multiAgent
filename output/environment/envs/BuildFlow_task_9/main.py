import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class SubcontractorCreate(BaseModel):
    name: str
    license_number: str
    insurance_expiry_date: str
    w9_document_id: str


class SubcontractorResponse(BaseModel):
    id: int
    name: str
    license_number: str
    insurance_expiry_date: str
    w9_document_id: str


class AssignSubcontractorRequest(BaseModel):
    subcontractor_id: int


class AssignSubcontractorResponse(BaseModel):
    task_id: int
    subcontractor_id: int


@app.post("/subcontractors", response_model=SubcontractorResponse, operation_id="create_subcontractor")
def create_subcontractor(payload: SubcontractorCreate):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO subcontractors (name, license_number, insurance_expiry_date, w9_document_id, created_at, updated_at)
                VALUES (:name, :license_number, :insurance_expiry_date, :w9_document_id, :now, :now)
            """),
            {
                "name": payload.name,
                "license_number": payload.license_number,
                "insurance_expiry_date": payload.insurance_expiry_date,
                "w9_document_id": payload.w9_document_id,
                "now": now,
            },
        )
        sub_id = result.lastrowid

        # Auto-assign the new subcontractor to all upcoming 'Mechanical' phase tasks
        # across active projects where no subcontractor is yet assigned and 
        # budget code '23 00 00 — HVAC' applies.
        conn.execute(
            text("""
                UPDATE tasks
                SET subcontractor_id = :sub_id, updated_at = :now
                WHERE status = 'upcoming'
                  AND phase = 'Mechanical'
                  AND subcontractor_id IS NULL
                  AND budget_code_id = (SELECT id FROM budget_codes WHERE code = '23 00 00 — HVAC')
                  AND project_id IN (SELECT id FROM projects WHERE status = 'active')
            """),
            {
                "sub_id": sub_id,
                "now": now,
            }
        )

        row = conn.execute(
            text("""
                SELECT id, name, license_number, insurance_expiry_date, w9_document_id
                FROM subcontractors
                WHERE id = :id
            """),
            {"id": sub_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created subcontractor")

    return SubcontractorResponse(
        id=row.id,
        name=row.name,
        license_number=row.license_number,
        insurance_expiry_date=row.insurance_expiry_date,
        w9_document_id=row.w9_document_id,
    )


@app.put("/tasks/{task_id}/subcontractor", response_model=AssignSubcontractorResponse, operation_id="assign_subcontractor_to_task")
def assign_subcontractor_to_task(task_id: int, payload: AssignSubcontractorRequest):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with engine.begin() as conn:
        # Verify task exists
        task_row = conn.execute(
            text("SELECT id FROM tasks WHERE id = :task_id"),
            {"task_id": task_id},
        ).fetchone()
        if not task_row:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Verify subcontractor exists
        sub_row = conn.execute(
            text("SELECT id FROM subcontractors WHERE id = :sub_id"),
            {"sub_id": payload.subcontractor_id},
        ).fetchone()
        if not sub_row:
            raise HTTPException(status_code=404, detail=f"Subcontractor {payload.subcontractor_id} not found")

        conn.execute(
            text("""
                UPDATE tasks
                SET subcontractor_id = :sub_id, updated_at = :now
                WHERE id = :task_id
            """),
            {
                "sub_id": payload.subcontractor_id,
                "task_id": task_id,
                "now": now,
            },
        )

    return AssignSubcontractorResponse(
        task_id=task_id,
        subcontractor_id=payload.subcontractor_id,
    )
