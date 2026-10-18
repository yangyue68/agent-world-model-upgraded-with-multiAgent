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


class CommentRequest(BaseModel):
    line_number: Optional[int] = None
    comment_text: str
    severity: str = Field(..., description="Severity level: Minor, Major, or Critical")


class ScoreUpdateRequest(BaseModel):
    score: float


@app.put("/submissions/{submission_id}/claim", operation_id="claim_submission")
def claim_submission(submission_id: int = Path(...)):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    db = SessionLocal()
    try:
        # Check if submission exists and is unclaimed
        result = db.execute(
            text("""
                SELECT id, claimed_by 
                FROM submissions 
                WHERE id = :sid
            """),
            {"sid": submission_id}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Submission not found")
        
        sub_id, claimed_by = result
        
        if claimed_by is not None:
            raise HTTPException(status_code=409, detail="Submission already claimed")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        db.execute(
            text("""
                UPDATE submissions 
                SET claimed_by = :uid, claimed_at = :now, updated_at = :now
                WHERE id = :sid AND claimed_by IS NULL
            """),
            {"uid": current_user_id, "now": now, "sid": submission_id}
        )
        db.commit()

        return {
            "id": sub_id,
            "claimed_by": current_user_id,
            "claimed_at": now
        }
    finally:
        db.close()


@app.post("/submissions/{submission_id}/comments", operation_id="add_comment")
def add_comment(
    submission_id: int = Path(...),
    body: CommentRequest = Body(...)
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    # Validate severity against allowed values from schema CHECK constraint
    if body.severity not in ["Minor", "Major", "Critical"]:
        raise HTTPException(status_code=422, detail="Invalid severity value")

    db = SessionLocal()
    try:
        # Verify submission exists
        sub_exists = db.execute(
            text("SELECT 1 FROM submissions WHERE id = :sid"),
            {"sid": submission_id}
        ).fetchone()
        if not sub_exists:
            raise HTTPException(status_code=404, detail="Submission not found")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        res = db.execute(
            text("""
                INSERT INTO comments (submission_id, user_id, line_number, comment_text, severity, created_at, updated_at)
                VALUES (:sid, :uid, :ln, :ct, :sev, :now, :now)
            """),
            {
                "sid": submission_id,
                "uid": current_user_id,
                "ln": body.line_number,
                "ct": body.comment_text,
                "sev": body.severity,
                "now": now
            }
        )
        db.commit()
        new_id = res.lastrowid

        return {
            "id": new_id,
            "line_number": body.line_number,
            "comment_text": body.comment_text,
            "severity": body.severity
        }
    finally:
        db.close()


@app.patch("/submissions/{submission_id}/scores/{rubric_criterion_id}", operation_id="update_score")
def update_score(
    submission_id: int = Path(...),
    rubric_criterion_id: int = Path(...),
    body: ScoreUpdateRequest = Body(...)
):
    db = SessionLocal()
    try:
        # Verify submission exists
        sub_exists = db.execute(
            text("SELECT 1 FROM submissions WHERE id = :sid"),
            {"sid": submission_id}
        ).fetchone()
        if not sub_exists:
            raise HTTPException(status_code=404, detail="Submission not found")

        # Verify rubric criterion exists
        crit_exists = db.execute(
            text("SELECT 1 FROM rubric_criteria WHERE id = :cid"),
            {"cid": rubric_criterion_id}
        ).fetchone()
        if not crit_exists:
            raise HTTPException(status_code=404, detail="Rubric criterion not found")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Try to find existing score row
        existing = db.execute(
            text("""
                SELECT id FROM submission_scores 
                WHERE submission_id = :sid AND rubric_criterion_id = :cid
            """),
            {"sid": submission_id, "cid": rubric_criterion_id}
        ).fetchone()

        if existing:
            score_id = existing[0]
            db.execute(
                text("""
                    UPDATE submission_scores 
                    SET score = :score, updated_at = :now
                    WHERE id = :id
                """),
                {"score": body.score, "now": now, "id": score_id}
            )
        else:
            res = db.execute(
                text("""
                    INSERT INTO submission_scores (submission_id, rubric_criterion_id, score, created_at, updated_at)
                    VALUES (:sid, :cid, :score, :now, :now)
                """),
                {"sid": submission_id, "cid": rubric_criterion_id, "score": body.score, "now": now}
            )
            score_id = res.lastrowid
            
        db.commit()

        return {
            "id": score_id,
            "score": body.score
        }
    finally:
        db.close()
