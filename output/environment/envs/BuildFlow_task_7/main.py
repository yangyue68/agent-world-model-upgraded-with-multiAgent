import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class AssignSubcontractorRequest(BaseModel):
    subcontractor_name: str
    start_date: date
    due_date: date
    priority: str


class TaskResponse(BaseModel):
    task_id: int
    assigned_subcontractor_id: int
    start_date: date
    due_date: date
    priority: str


class AttachmentRequest(BaseModel):
    file_id: str
    file_name: Optional[str] = None
    description: Optional[str] = None


class AttachmentResponse(BaseModel):
    attachment_id: int
    task_id: int
    file_id: str


class DependencyRequest(BaseModel):
    depends_on_task_id: int


class DependencyResponse(BaseModel):
    dependency_id: int
    task_id: int
    depends_on_task_id: int


@app.put("/tasks/{task_id}", response_model=TaskResponse, operation_id="assign_subcontractor_to_task")
def assign_subcontractor_to_task(task_id: int, payload: AssignSubcontractorRequest):
    db = SessionLocal()
    try:
        # Validate priority against CHECK constraint values
        if payload.priority not in ("Low", "Medium", "High", "Critical"):
            raise HTTPException(status_code=422, detail=f"Invalid priority '{payload.priority}'. Must be one of Low, Medium, High, Critical.")

        # Find subcontractor ID by name
        sub_res = db.execute(text("SELECT id FROM subcontractors WHERE name = :name"), {"name": payload.subcontractor_name})
        sub_row = sub_res.fetchone()
        if not sub_row:
            raise HTTPException(status_code=404, detail=f"Subcontractor '{payload.subcontractor_name}' not found.")
        subcontractor_id = sub_row[0]

        # Verify task exists and belongs to correct project context (optional but good practice)
        task_res = db.execute(text("SELECT id, project_id FROM tasks WHERE id = :tid"), {"tid": task_id})
        task_row = task_res.fetchone()
        if not task_row:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found.")

        # Update the task
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            text("""
                UPDATE tasks 
                SET assigned_subcontractor_id = :sid, 
                    start_date = :sd, 
                    due_date = :dd, 
                    priority = :p,
                    updated_at = :ua
                WHERE id = :tid
            """),
            {
                "sid": subcontractor_id,
                "sd": payload.start_date.isoformat(),
                "dd": payload.due_date.isoformat(),
                "p": payload.priority,
                "ua": now_str,
                "tid": task_id
            }
        )
        db.commit()

        return TaskResponse(
            task_id=task_id,
            assigned_subcontractor_id=subcontractor_id,
            start_date=payload.start_date,
            due_date=payload.due_date,
            priority=payload.priority
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/tasks/{task_id}/attachments", response_model=AttachmentResponse, operation_id="attach_revised_blueprint")
def attach_revised_blueprint(task_id: int, payload: AttachmentRequest):
    db = SessionLocal()
    try:
        # Get current user from environment
        user_id_env = os.environ.get("AWM_CURRENT_USER_ID")
        if not user_id_env:
            raise HTTPException(status_code=401, detail="User not authenticated.")
        uploaded_by = int(user_id_env)

        # Verify task exists
        task_res = db.execute(text("SELECT id FROM tasks WHERE id = :tid"), {"tid": task_id})
        if not task_res.fetchone():
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found.")

        # Insert attachment
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        res = db.execute(
            text("""
                INSERT INTO attachments (task_id, file_id, file_name, description, uploaded_by, created_at, updated_at)
                VALUES (:tid, :fid, :fname, :desc, :uid, :ca, :ua)
            """),
            {
                "tid": task_id,
                "fid": payload.file_id,
                "fname": payload.file_name,
                "desc": payload.description,
                "uid": uploaded_by,
                "ca": now_str,
                "ua": now_str
            }
        )
        db.commit()
        
        attachment_id = res.lastrowid
        
        return AttachmentResponse(
            attachment_id=attachment_id,
            task_id=task_id,
            file_id=payload.file_id
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/tasks/{task_id}/dependencies", response_model=DependencyResponse, operation_id="link_dependency_to_task")
def link_dependency_to_task(task_id: int, payload: DependencyRequest):
    db = SessionLocal()
    try:
        # Check self-dependency
        if task_id == payload.depends_on_task_id:
            raise HTTPException(status_code=422, detail="A task cannot depend on itself.")

        # Verify both tasks exist
        t1_res = db.execute(text("SELECT id FROM tasks WHERE id = :tid"), {"tid": task_id})
        if not t1_res.fetchone():
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found.")
            
        t2_res = db.execute(text("SELECT id FROM tasks WHERE id = :tid"), {"tid": payload.depends_on_task_id})
        if not t2_res.fetchone():
            raise HTTPException(status_code=404, detail=f"Depends-on task {payload.depends_on_task_id} not found.")

        # Insert dependency
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        res = db.execute(
            text("""
                INSERT INTO task_dependencies (task_id, depends_on_task_id, created_at)
                VALUES (:tid, :dtid, :ca)
            """),
            {
                "tid": task_id,
                "dtid": payload.depends_on_task_id,
                "ca": now_str
            }
        )
        db.commit()
        
        dependency_id = res.lastrowid

        return DependencyResponse(
            dependency_id=dependency_id,
            task_id=task_id,
            depends_on_task_id=payload.depends_on_task_id
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
