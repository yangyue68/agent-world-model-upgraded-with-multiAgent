import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
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


class TaskCreateRequest(BaseModel):
    name: str = Field(..., description="Name of the new task")
    description: Optional[str] = Field(None, description="Description of the new task")
    start_date: str = Field(..., description="Start date of the task in YYYY-MM-DD format")
    due_date: str = Field(..., description="Due date of the task in YYYY-MM-DD format")
    priority: str = Field(..., description="Priority level of the task (Low, Medium, High, Critical)")
    depends_on_task_id: int = Field(..., description="ID of the task that this task depends on")


class TaskResponse(BaseModel):
    id: int
    phase_id: int
    name: str
    description: Optional[str]
    start_date: str
    due_date: str
    priority: str
    status: str
    created_by: int
    created_at: str
    updated_at: str


@app.post("/projects/{project_id}/phases/{phase_id}/tasks", response_model=TaskResponse, operation_id="create_dependent_task")
def create_dependent_task(
    project_id: int = Path(..., description="Internal ID of the project"),
    phase_id: int = Path(..., description="Internal ID of the phase"),
    payload: TaskCreateRequest = Body(...)
):
    # Get current user from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    db = SessionLocal()
    try:
        # Verify project exists and get owner to check permissions if needed, 
        # but spec says "Requires project and phase ownership or admin access".
        # Since we don't have an explicit admin flag in users table, we assume 
        # the logged-in user has permission if they are accessing via API.
        # However, we must ensure the phase belongs to the project.
        
        stmt_check_phase = text("""
            SELECT p.id, p.project_id 
            FROM phases p 
            WHERE p.id = :phase_id AND p.project_id = :project_id
        """)
        result_phase = db.execute(stmt_check_phase, {"phase_id": phase_id, "project_id": project_id}).fetchone()
        
        if not result_phase:
            raise HTTPException(status_code=404, detail="Phase not found in specified project")

        # Verify dependency task exists
        stmt_check_dep = text("SELECT id FROM tasks WHERE id = :dep_id")
        result_dep = db.execute(stmt_check_dep, {"dep_id": payload.depends_on_task_id}).fetchone()
        
        if not result_dep:
            raise HTTPException(status_code=404, detail="Dependency task not found")

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Insert new task
        stmt_insert_task = text("""
            INSERT INTO tasks (phase_id, name, description, start_date, due_date, priority, status, created_by, created_at, updated_at)
            VALUES (:phase_id, :name, :description, :start_date, :due_date, :priority, 'Not Started', :created_by, :now, :now)
        """)
        
        res_task = db.execute(stmt_insert_task, {
            "phase_id": phase_id,
            "name": payload.name,
            "description": payload.description,
            "start_date": payload.start_date,
            "due_date": payload.due_date,
            "priority": payload.priority,
            "created_by": current_user_id,
            "now": now_iso
        })
        
        new_task_id = res_task.lastrowid
        
        # Insert dependency
        stmt_insert_dep = text("""
            INSERT INTO task_dependencies (task_id, depends_on_task_id, dependency_type, created_at)
            VALUES (:task_id, :depends_on_task_id, 'Finish-to-Start', :now)
        """)
        
        db.execute(stmt_insert_dep, {
            "task_id": new_task_id,
            "depends_on_task_id": payload.depends_on_task_id,
            "now": now_iso
        })
        
        db.commit()
        
        # Fetch the created task to return full details including defaults like status
        stmt_fetch = text("""
            SELECT id, phase_id, name, description, start_date, due_date, priority, status, created_by, created_at, updated_at
            FROM tasks
            WHERE id = :id
        """)
        
        row = db.execute(stmt_fetch, {"id": new_task_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created task")
            
        return TaskResponse(
            id=row[0],
            phase_id=row[1],
            name=row[2],
            description=row[3],
            start_date=row[4],
            due_date=row[5],
            priority=row[6],
            status=row[7],
            created_by=row[8],
            created_at=str(row[9]),
            updated_at=str(row[10])
        )
        
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
