import os
from datetime import datetime
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CreateAdHocTaskRequest(BaseModel):
    title: str
    description: Optional[str] = None
    assignee_name: str
    protocol_name: str
    protocol_version: str
    due_date: str
    priority: Literal["Low", "Medium", "High", "Critical"]
    status: Literal["Not Started", "In Progress", "Completed", "Blocked", "Cancelled"]


class TaskResponse(BaseModel):
    id: int
    title: str
    assignee_id: int
    protocol_id: Optional[int]
    due_date: str
    priority: str
    status: str
    created_by: int


def parse_iso_datetime(date_str: str) -> datetime:
    """Parse ISO-8601 date string, handling both T...Z and space-separated forms."""
    # Replace 'T' with space if present, and remove trailing 'Z'
    cleaned = date_str.replace("T", " ").replace("Z", "")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str}")


@app.post("/tasks", operation_id="create_ad_hoc_task", response_model=TaskResponse)
def create_ad_hoc_task(request: CreateAdHocTaskRequest):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=500, detail="Current user ID not available")
    
    try:
        current_user_id_int = int(current_user_id)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID")

    # Parse due date
    try:
        due_dt = parse_iso_datetime(request.due_date)
        due_date_str = due_dt.strftime("%Y-%m-%d %H:%M:%S")
    except HTTPException:
        raise

    with engine.connect() as conn:
        # Find assignee by full name
        result = conn.execute(
            text("SELECT id FROM users WHERE full_name = :full_name"),
            {"full_name": request.assignee_name}
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Assignee '{request.assignee_name}' not found")
        assignee_id = row[0]

        # Find protocol by name and version
        result = conn.execute(
            text("SELECT id FROM protocols WHERE name = :name AND version = :version"),
            {"name": request.protocol_name, "version": request.protocol_version}
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Protocol '{request.protocol_name}' version '{request.protocol_version}' not found"
            )
        protocol_id = row[0]

        # Insert new task
        insert_result = conn.execute(
            text("""
                INSERT INTO tasks (title, description, assignee_id, protocol_id, due_date, priority, status, created_by)
                VALUES (:title, :description, :assignee_id, :protocol_id, :due_date, :priority, :status, :created_by)
            """),
            {
                "title": request.title,
                "description": request.description,
                "assignee_id": assignee_id,
                "protocol_id": protocol_id,
                "due_date": due_date_str,
                "priority": request.priority,
                "status": request.status,
                "created_by": current_user_id_int,
            }
        )
        conn.commit()

        task_id = insert_result.lastrowid

        # Fetch the created task to return
        result = conn.execute(
            text("""
                SELECT id, title, assignee_id, protocol_id, due_date, priority, status, created_by
                FROM tasks WHERE id = :task_id
            """),
            {"task_id": task_id}
        )
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created task")

        return TaskResponse(
            id=row[0],
            title=row[1],
            assignee_id=row[2],
            protocol_id=row[3],
            due_date=str(row[4]),
            priority=row[5],
            status=row[6],
            created_by=row[7],
        )
