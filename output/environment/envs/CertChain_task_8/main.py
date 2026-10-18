import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    create_engine,
    text,
)
from sqlalchemy.orm import Session, declarative_base
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
Base = declarative_base()


# --- SQLAlchemy Models for Reference Only (Schema Mapping) ---

class ExamModel(Base):
    __tablename__ = "exams"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False)
    certification_id = Column(Integer, ForeignKey("certifications.id"), nullable=False)
    test_center_id = Column(Integer, ForeignKey("test_centers.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="scheduled")
    result = Column(String)
    score = Column(Float)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


# --- Pydantic Schemas ---

class ScheduleExamRequest(BaseModel):
    candidate_id: int
    certification_id: int
    test_center_id: int
    scheduled_at: str  # ISO-8601 string
    result: str
    score: float


class ScheduleExamResponse(BaseModel):
    id: int
    candidate_id: int
    certification_id: int
    test_center_id: int
    scheduled_at: str
    status: str
    result: Optional[str] = None
    score: Optional[float] = None
    created_by: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


# --- App Setup ---

app = FastAPI(title="CertChain API", description="Schedule exams and manage results")


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both 'T' and space separators."""
    dt_str = dt_str.replace("Z", "+00:00")
    if "T" in dt_str:
        return datetime.fromisoformat(dt_str)
    else:
        # Handle space-separated format like "2024-11-15 10:00:00"
        return datetime.fromisoformat(dt_str.replace(" ", "T"))


@app.post("/exams", operation_id="schedule_exam", response_model=ScheduleExamResponse)
def schedule_exam(request: ScheduleExamRequest):
    """Schedule an exam for a candidate"""
    
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=500, detail="User context missing")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user context")

    # Parse the scheduled datetime
    try:
        scheduled_dt = parse_datetime(request.scheduled_at)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid datetime format")

    # Validate enum values against CHECK constraints
    valid_statuses = ['scheduled', 'completed', 'cancelled', 'no_show']
    valid_results = ['pass', 'fail']

    if request.result not in valid_results:
        raise HTTPException(status_code=400, detail=f"Result must be one of {valid_results}")
    
    # Determine status based on result. 
    # If result is provided, it implies the exam has happened or is being recorded as completed.
    # The spec says "record the result as 'pass'", implying completion.
    status = "completed"

    # Use raw SQL to insert to ensure exact control over types and avoid ORM overhead/bugs
    # Also ensures we don't trigger any hooks or modify schema
    conn = engine.connect()
    try:
        # Check if candidate exists
        candidate_check = conn.execute(
            text("SELECT id FROM candidates WHERE id = :cid"),
            {"cid": request.candidate_id}
        ).fetchone()
        if not candidate_check:
            raise HTTPException(status_code=404, detail="Candidate not found")

        # Check if certification exists
        cert_check = conn.execute(
            text("SELECT id FROM certifications WHERE id = :cid"),
            {"cid": request.certification_id}
        ).fetchone()
        if not cert_check:
            raise HTTPException(status_code=404, detail="Certification not found")

        # Check if test center exists
        tc_check = conn.execute(
            text("SELECT id FROM test_centers WHERE id = :tcid"),
            {"tcid": request.test_center_id}
        ).fetchone()
        if not tc_check:
            raise HTTPException(status_code=404, detail="Test center not found")

        # Insert the exam record
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_sql = text("""
            INSERT INTO exams (candidate_id, certification_id, test_center_id, scheduled_at, status, result, score, created_by, created_at, updated_at)
            VALUES (:candidate_id, :certification_id, :test_center_id, :scheduled_at, :status, :result, :score, :created_by, :created_at, :updated_at)
        """)
        
        conn.execute(insert_sql, {
            "candidate_id": request.candidate_id,
            "certification_id": request.certification_id,
            "test_center_id": request.test_center_id,
            "scheduled_at": scheduled_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "result": request.result,
            "score": request.score,
            "created_by": current_user_id,
            "created_at": now_utc,
            "updated_at": now_utc
        })
        
        conn.commit()

        # Retrieve the inserted ID
        last_insert_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
        
        # Fetch the full record to return
        select_sql = text("""
            SELECT id, candidate_id, certification_id, test_center_id, scheduled_at, status, result, score, created_by, created_at, updated_at
            FROM exams
            WHERE id = :eid
        """)
        
        row = conn.execute(select_sql, {"eid": last_insert_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created exam")

        # Map row to response model
        response_data = {
            "id": row[0],
            "candidate_id": row[1],
            "certification_id": row[2],
            "test_center_id": row[3],
            "scheduled_at": row[4],
            "status": row[5],
            "result": row[6],
            "score": row[7],
            "created_by": row[8],
            "created_at": row[9],
            "updated_at": row[10]
        }
        
        return ScheduleExamResponse(**response_data)

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
