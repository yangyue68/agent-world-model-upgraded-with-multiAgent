import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UpdateTaskRequest(BaseModel):
    status: str
    comment: str


class AddAttachmentRequest(BaseModel):
    file_id: str
    file_name: Optional[str] = None


@app.put("/tasks/{task_id}", operation_id="update_task_status_and_add_comment")
def update_task_status_and_add_comment(task_id: int, payload: UpdateTaskRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Validate task exists
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id FROM tasks WHERE id = :tid"), {"tid": task_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Task not found")

        # Update task status
        conn.execute(
            text("UPDATE tasks SET status = :status, updated_at = CURRENT_TIMESTAMP WHERE id = :tid"),
            {"status": payload.status, "tid": task_id}
        )

        # Insert comment
        res_comment = conn.execute(
            text("""
                INSERT INTO comments (task_id, user_id, body, created_at, updated_at)
                VALUES (:tid, :uid, :body, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {"tid": task_id, "uid": current_user_id, "body": payload.comment}
        )
        comment_id = res_comment.lastrowid
        
        conn.commit()

    return {
        "task_id": task_id,
        "status": payload.status,
        "comment_id": comment_id
    }


@app.post("/tasks/{task_id}/attachments", operation_id="add_attachment_to_task")
def add_attachment_to_task(task_id: int, payload: AddAttachmentRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.connect() as conn:
        # Validate task exists
        result = conn.execute(text("SELECT id FROM tasks WHERE id = :tid"), {"tid": task_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Task not found")

        # Check for duplicate attachment (UNIQUE constraint on task_id, file_id)
        dup_check = conn.execute(
            text("SELECT id FROM attachments WHERE task_id = :tid AND file_id = :fid"),
            {"tid": task_id, "fid": payload.file_id}
        )
        if dup_check.fetchone() is not None:
            raise HTTPException(status_code=409, detail="Attachment already exists for this task and file")

        # Insert attachment
        res_att = conn.execute(
            text("""
                INSERT INTO attachments (task_id, file_id, file_name, uploaded_by, created_at, updated_at)
                VALUES (:tid, :fid, :fname, :uid, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "tid": task_id,
                "fid": payload.file_id,
                "fname": payload.file_name,
                "uid": current_user_id
            }
        )
        attachment_id = res_att.lastrowid
        
        conn.commit()

    return {
        "attachment_id": attachment_id,
        "task_id": task_id,
        "file_id": payload.file_id
    }
