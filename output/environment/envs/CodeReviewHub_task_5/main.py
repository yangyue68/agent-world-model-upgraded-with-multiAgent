import os
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class LineCommentCreate(BaseModel):
    file_id: int = Field(..., description="Internal ID of the file within the submission")
    line_number: int = Field(..., gt=0, description="Line number where the comment is placed (must be greater than 0)")
    comment_text: str = Field(..., description="Text content of the comment")
    severity: str = Field(..., description="Severity level of the comment (info, minor, major, critical)")


class ResubmissionRequestCreate(BaseModel):
    deadline: str = Field(..., description="Deadline date for the resubmission in YYYY-MM-DD format")


@app.post("/submissions/{submission_id}/line_comments", operation_id="add_line_comment")
def add_line_comment(
    submission_id: str = Path(..., description="Unique identifier of the submission"),
    payload: LineCommentCreate = Body(...)
):
    # Get current user from environment
    author_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not author_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        author_id = int(author_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user ID format")

    db = SessionLocal()
    try:
        # Verify submission exists
        sub_check = db.execute(text("SELECT id FROM submissions WHERE id = :sid"), {"sid": submission_id}).fetchone()
        if not sub_check:
            raise HTTPException(status_code=404, detail="Submission not found")

        # Verify file belongs to this submission and exists
        file_check = db.execute(
            text("SELECT id FROM submission_files WHERE id = :fid AND submission_id = :sid"),
            {"fid": payload.file_id, "sid": submission_id}
        ).fetchone()
        if not file_check:
            raise HTTPException(status_code=404, detail="File not found or does not belong to this submission")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Insert line comment
        result = db.execute(
            text("""
                INSERT INTO line_comments 
                (submission_id, file_id, line_number, comment_text, severity, author_id, created_at, updated_at)
                VALUES (:sid, :fid, :ln, :ct, :sev, :aid, :now, :now)
            """),
            {
                "sid": submission_id,
                "fid": payload.file_id,
                "ln": payload.line_number,
                "ct": payload.comment_text,
                "sev": payload.severity,
                "aid": author_id,
                "now": now
            }
        )
        new_id = result.lastrowid
        
        db.commit()

        # Fetch the created record to return full details including defaults
        record = db.execute(
            text("""
                SELECT id, submission_id, file_id, line_number, comment_text, severity, author_id, created_at, updated_at
                FROM line_comments
                WHERE id = :id
            """),
            {"id": new_id}
        ).fetchone()

        if not record:
            raise HTTPException(status_code=500, detail="Failed to retrieve created comment")

        return {
            "id": record.id,
            "submission_id": record.submission_id,
            "file_id": record.file_id,
            "line_number": record.line_number,
            "comment_text": record.comment_text,
            "severity": record.severity,
            "author_id": record.author_id,
            "created_at": record.created_at,
            "updated_at": record.updated_at
        }

    finally:
        db.close()


@app.post("/submissions/{submission_id}/resubmission_requests", operation_id="request_resubmission")
def request_resubmission(
    submission_id: str = Path(..., description="Unique identifier of the submission"),
    payload: ResubmissionRequestCreate = Body(...)
):
    # Get current user from environment
    requested_by_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not requested_by_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        requested_by = int(requested_by_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user ID format")

    # Validate deadline format
    try:
        parsed_deadline = datetime.strptime(payload.deadline, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid deadline format. Expected YYYY-MM-DD.")

    db = SessionLocal()
    try:
        # Verify submission exists
        sub_check = db.execute(text("SELECT id FROM submissions WHERE id = :sid"), {"sid": submission_id}).fetchone()
        if not sub_check:
            raise HTTPException(status_code=404, detail="Submission not found")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
        # Update submission status to 'changes_requested'
        db.execute(
            text("UPDATE submissions SET status = 'changes_requested', updated_at = :now WHERE id = :sid"),
            {"now": now, "sid": submission_id}
        )

        # Insert resubmission request
        result = db.execute(
            text("""
                INSERT INTO resubmission_requests 
                (submission_id, requested_by, deadline, status, created_at, updated_at)
                VALUES (:sid, :rb, :dl, 'pending', :now, :now)
            """),
            {
                "sid": submission_id,
                "rb": requested_by,
                "dl": parsed_deadline.isoformat(),
                "now": now
            }
        )
        new_id = result.lastrowid
        
        db.commit()

        # Fetch the created record
        record = db.execute(
            text("""
                SELECT id, submission_id, requested_by, deadline, status, created_at, updated_at
                FROM resubmission_requests
                WHERE id = :id
            """),
            {"id": new_id}
        ).fetchone()

        if not record:
            raise HTTPException(status_code=500, detail="Failed to retrieve created resubmission request")

        return {
            "id": record.id,
            "submission_id": record.submission_id,
            "requested_by": record.requested_by,
            "deadline": record.deadline,
            "status": record.status,
            "created_at": record.created_at,
            "updated_at": record.updated_at
        }

    finally:
        db.close()
