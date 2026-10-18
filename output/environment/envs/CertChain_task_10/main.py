import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")  # Placeholder; actual value injected by runtime

app = FastAPI(title="CertChain API")

engine = create_engine(DATABASE_PATH)


class ExamResultRequest(BaseModel):
    candidate_id: str = Field(..., description="The unique identifier of the candidate")
    exam_id: int = Field(..., description="The ID of the exam taken")
    test_center_id: int = Field(..., description="The ID of the test center where the exam was taken")
    score: float = Field(..., ge=0, le=100, description="The score achieved by the candidate (between 0 and 100)")
    result: str = Field(..., pattern=r"^(pass|fail)$", description="The result of the exam ('pass' or 'fail')")
    exam_date: str = Field(..., description="The date when the exam was taken (YYYY-MM-DD)")
    created_by: int = Field(..., description="The ID of the user who recorded the result")


class ExamResultResponse(BaseModel):
    id: int
    candidate_id: str
    exam_id: int
    test_center_id: int
    score: float
    result: str
    exam_date: str
    created_by: int
    created_at: str
    updated_at: str


@app.post("/exam-results", operation_id="record_exam_result", response_model=ExamResultResponse)
def record_exam_result(payload: ExamResultRequest):
    """Record an exam result for a candidate."""
    # Validate that the candidate exists
    with engine.connect() as conn:
        candidate_exists = conn.execute(
            text("SELECT 1 FROM candidates WHERE id = :cid"), {"cid": payload.candidate_id}
        ).fetchone()
        if not candidate_exists:
            raise HTTPException(status_code=404, detail=f"Candidate {payload.candidate_id} not found")

        # Validate that the exam exists
        exam_exists = conn.execute(
            text("SELECT 1 FROM exams WHERE id = :eid"), {"eid": payload.exam_id}
        ).fetchone()
        if not exam_exists:
            raise HTTPException(status_code=404, detail=f"Exam {payload.exam_id} not found")

        # Validate that the test center exists
        tc_exists = conn.execute(
            text("SELECT 1 FROM test_centers WHERE id = :tcid"), {"tcid": payload.test_center_id}
        ).fetchone()
        if not tc_exists:
            raise HTTPException(status_code=404, detail=f"Test center {payload.test_center_id} not found")

        # Validate that the creating user exists
        user_exists = conn.execute(
            text("SELECT 1 FROM users WHERE id = :uid"), {"uid": payload.created_by}
        ).fetchone()
        if not user_exists:
            raise HTTPException(status_code=404, detail=f"User {payload.created_by} not found")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        insert_stmt = text("""
            INSERT INTO exam_results (candidate_id, exam_id, test_center_id, score, result, exam_date, created_by, created_at, updated_at)
            VALUES (:candidate_id, :exam_id, :test_center_id, :score, :result, :exam_date, :created_by, :created_at, :updated_at)
        """)

        conn.execute(insert_stmt, {
            "candidate_id": payload.candidate_id,
            "exam_id": payload.exam_id,
            "test_center_id": payload.test_center_id,
            "score": payload.score,
            "result": payload.result,
            "exam_date": payload.exam_date,
            "created_by": payload.created_by,
            "created_at": now,
            "updated_at": now,
        })
        conn.commit()

        # Fetch the newly inserted row to return it
        new_row = conn.execute(
            text("SELECT * FROM exam_results ORDER BY id DESC LIMIT 1")
        ).fetchone()

        return ExamResultResponse(
            id=new_row[0],
            candidate_id=new_row[1],
            exam_id=new_row[2],
            test_center_id=new_row[3],
            score=new_row[4],
            result=new_row[5],
            exam_date=str(new_row[6]),
            created_by=new_row[7],
            created_at=str(new_row[8]),
            updated_at=str(new_row[9]),
        )
