import os
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from datetime import datetime

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()

class UpdateExamRegistrationRequest(BaseModel):
    new_session_id: int
    outcome: str = Field(..., pattern="^(pass|fail)$")
    score: float = Field(..., ge=0, le=100)

class ExamRegistrationResponse(BaseModel):
    id: int
    candidate_id: int
    candidate_name: str
    exam_title: str
    current_session_start: str
    current_session_end: str
    status: str
    outcome: Optional[str] = None
    score: Optional[float] = None

class ExamSessionResponse(BaseModel):
    id: int
    scheduled_start: str
    scheduled_end: str
    session_type: str
    location: Optional[str] = None

class SuccessResponse(BaseModel):
    success: bool
    message: str


@app.put("/exam_registrations/{registration_id}", response_model=SuccessResponse, operation_id="update_exam_registration")
def update_exam_registration(registration_id: int, request: UpdateExamRegistrationRequest):
    """
    Update an exam registration with new session and results.
    """
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=400, detail="Missing user context")
    
    try:
        recorded_by_user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    # Verify registration exists
    with engine.connect() as conn:
        reg_exists = conn.execute(
            text("SELECT id FROM exam_registrations WHERE id = :id"),
            {"id": registration_id}
        ).fetchone()
        
        if not reg_exists:
            raise HTTPException(status_code=404, detail="Exam registration not found")

        # Verify new session exists
        session_exists = conn.execute(
            text("SELECT id FROM exam_sessions WHERE id = :id"),
            {"id": request.new_session_id}
        ).fetchone()
        
        if not session_exists:
            raise HTTPException(status_code=404, detail="Exam session not found")

        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        
        # Update the registration
        conn.execute(
            text("""
                UPDATE exam_registrations 
                SET exam_session_id = :new_session_id,
                    outcome = :outcome,
                    score = :score,
                    status = 'completed',
                    recorded_by_user_id = :recorded_by_user_id,
                    recorded_at = :recorded_at,
                    updated_at = :updated_at
                WHERE id = :registration_id
            """),
            {
                "new_session_id": request.new_session_id,
                "outcome": request.outcome,
                "score": request.score,
                "recorded_by_user_id": recorded_by_user_id,
                "recorded_at": now,
                "updated_at": now,
                "registration_id": registration_id
            }
        )
        conn.commit()

    return SuccessResponse(success=True, message="Exam registration updated successfully")


@app.get("/exam_registrations/{registration_id}", response_model=ExamRegistrationResponse, operation_id="get_exam_registration_details")
def get_exam_registration_details(registration_id: int):
    """
    Get detailed information about an exam registration.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT 
                    er.id,
                    er.candidate_id,
                    c.first_name || ' ' || c.last_name as candidate_name,
                    e.title as exam_title,
                    es.scheduled_start as current_session_start,
                    es.scheduled_end as current_session_end,
                    er.status,
                    er.outcome,
                    er.score
                FROM exam_registrations er
                JOIN candidates c ON er.candidate_id = c.id
                JOIN exam_sessions es ON er.exam_session_id = es.id
                JOIN exams e ON es.exam_id = e.id
                WHERE er.id = :registration_id
            """),
            {"registration_id": registration_id}
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Exam registration not found")

        return ExamRegistrationResponse(
            id=row[0],
            candidate_id=row[1],
            candidate_name=row[2],
            exam_title=row[3],
            current_session_start=str(row[4]),
            current_session_end=str(row[5]),
            status=row[6],
            outcome=row[7],
            score=float(row[8]) if row[8] is not None else None
        )


@app.get("/exam_sessions", response_model=List[ExamSessionResponse], operation_id="find_available_exam_sessions")
def find_available_exam_sessions(
    exam_id: int = Query(..., description="The ID of the exam to find sessions for"),
    session_type: Optional[str] = Query(None, description="Filter sessions by type ('online_proctored', 'in_person', 'hybrid')"),
    start_date: Optional[str] = Query(None, description="Filter sessions starting on or after this date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Filter sessions ending on or before this date (YYYY-MM-DD)")
):
    """
    Find available exam sessions for a specific exam.
    """
    query = """
        SELECT 
            id,
            scheduled_start,
            scheduled_end,
            session_type,
            location
        FROM exam_sessions
        WHERE exam_id = :exam_id
    """
    params = {"exam_id": exam_id}

    if session_type:
        query += " AND session_type = :session_type"
        params["session_type"] = session_type
    
    if start_date:
        # Parse start_date to ensure it's valid and compare against scheduled_start
        try:
            dt_start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        except ValueError:
             # Try YYYY-MM-DD format
             dt_start = datetime.strptime(start_date, "%Y-%m-%d")
        
        query += " AND scheduled_start >= :start_dt"
        params["start_dt"] = dt_start.strftime("%Y-%m-%d %H:%M:%S")

    if end_date:
        try:
            dt_end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        except ValueError:
             dt_end = datetime.strptime(end_date, "%Y-%m-%d")
        
        query += " AND scheduled_end <= :end_dt"
        params["end_dt"] = dt_end.strftime("%Y-%m-%d %H:%M:%S")

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    result = []
    for row in rows:
        result.append(ExamSessionResponse(
            id=row[0],
            scheduled_start=str(row[1]),
            scheduled_end=str(row[2]),
            session_type=row[3],
            location=row[4]
        ))

    return result
