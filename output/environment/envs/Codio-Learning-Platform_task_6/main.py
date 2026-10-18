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


class EnrollmentRequest(BaseModel):
    student_email: str
    course_id: str
    cohort_id: str


class EnrollmentResponse(BaseModel):
    id: int
    user_id: int
    course_id: str
    cohort_id: str
    status: str
    enrolled_at: str
    created_at: str
    updated_at: str


@app.post("/enrollments", response_model=EnrollmentResponse, operation_id="create_enrollment")
def create_enrollment(payload: EnrollmentRequest):
    # Look up the user by email
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": payload.student_email}
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Student not found")
        user_id = row[0]

        # Verify course exists
        result = conn.execute(
            text("SELECT id FROM courses WHERE id = :course_id"),
            {"course_id": payload.course_id}
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Course not found")

        # Verify cohort exists and belongs to the specified course
        result = conn.execute(
            text("SELECT id, course_id FROM cohorts WHERE id = :cohort_id"),
            {"cohort_id": payload.cohort_id}
        )
        cohort_row = result.fetchone()
        if cohort_row is None:
            raise HTTPException(status_code=404, detail="Cohort not found")
        if cohort_row[1] != payload.course_id:
            raise HTTPException(status_code=400, detail="Cohort does not belong to the specified course")

        # Check for existing enrollment (UNIQUE constraint on user_id, course_id, cohort_id)
        result = conn.execute(
            text("""
                SELECT id FROM enrollments 
                WHERE user_id = :user_id AND course_id = :course_id AND cohort_id = :cohort_id
            """),
            {
                "user_id": user_id,
                "course_id": payload.course_id,
                "cohort_id": payload.cohort_id
            }
        )
        if result.fetchone() is not None:
            raise HTTPException(status_code=409, detail="Enrollment already exists")

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Insert new enrollment
        insert_result = conn.execute(
            text("""
                INSERT INTO enrollments (user_id, course_id, cohort_id, status, enrolled_at, created_at, updated_at)
                VALUES (:user_id, :course_id, :cohort_id, 'active', :now, :now, :now)
            """),
            {
                "user_id": user_id,
                "course_id": payload.course_id,
                "cohort_id": payload.cohort_id,
                "now": now_str
            }
        )
        conn.commit()
        enrollment_id = insert_result.lastrowid

        # Fetch the inserted record to return full details
        result = conn.execute(
            text("""
                SELECT id, user_id, course_id, cohort_id, status, enrolled_at, created_at, updated_at
                FROM enrollments
                WHERE id = :id
            """),
            {"id": enrollment_id}
        )
        final_row = result.fetchone()

    return EnrollmentResponse(
        id=final_row[0],
        user_id=final_row[1],
        course_id=final_row[2],
        cohort_id=final_row[3],
        status=final_row[4],
        enrolled_at=str(final_row[5]),
        created_at=str(final_row[6]),
        updated_at=str(final_row[7])
    )
