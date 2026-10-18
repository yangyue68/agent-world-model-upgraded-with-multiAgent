import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class BulkEnrollRequest(BaseModel):
    course_id: str
    cohort_id: str
    student_ids: List[str]


class FailedEnrollment(BaseModel):
    user_id: str
    error: str


class BulkEnrollResponse(BaseModel):
    enrollments_created: int
    failed_enrollments: List[FailedEnrollment]


@app.post("/enrollments/bulk", response_model=BulkEnrollResponse, operation_id="bulk_enroll_students")
def bulk_enroll_students(request: BulkEnrollRequest):
    """
    Bulk enroll students into a course cohort with pending-verification status.
    """
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        # In a real scenario, this might be an auth error, but per instructions 
        # we assume it's injected. If missing, we can't record performed_by correctly.
        # However, the spec says "read that environment variable at request time".
        # We will proceed assuming it exists or handle gracefully if needed.
        pass 

    db = SessionLocal()
    
    try:
        # Validate Course Exists
        course_check = db.execute(text("SELECT id FROM courses WHERE id = :cid"), {"cid": request.course_id}).fetchone()
        if not course_check:
            raise HTTPException(status_code=404, detail=f"Course {request.course_id} not found.")

        # Validate Cohort Exists and belongs to Course
        cohort_check = db.execute(
            text("SELECT id, course_id FROM cohorts WHERE id = :cohort_id AND course_id = :course_id"),
            {"cohort_id": request.cohort_id, "course_id": request.course_id}
        ).fetchone()
        
        if not cohort_check:
            raise HTTPException(status_code=404, detail=f"Cohort {request.cohort_id} not found for course {request.course_id}.")

        created_count = 0
        failed_list = []

        for student_id in request.student_ids:
            # Check if User Exists
            user_exists = db.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": student_id}).fetchone()
            if not user_exists:
                failed_list.append(FailedEnrollment(user_id=student_id, error="User does not exist"))
                continue

            # Check for existing enrollment (UNIQUE constraint on user_id, course_id, cohort_id)
            existing_enrollment = db.execute(
                text("SELECT id FROM enrollments WHERE user_id = :uid AND course_id = :cid AND cohort_id = :cohort_id"),
                {"uid": student_id, "cid": request.course_id, "cohort_id": request.cohort_id}
            ).fetchone()

            if existing_enrollment:
                failed_list.append(FailedEnrollment(user_id=student_id, error="Student already enrolled in this course/cohort combination"))
                continue

            # Insert Enrollment
            try:
                result = db.execute(
                    text("""
                        INSERT INTO enrollments (user_id, course_id, cohort_id, status, enrolled_at, created_at, updated_at)
                        VALUES (:uid, :cid, :cohort_id, 'pending-verification', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """),
                    {
                        "uid": student_id,
                        "cid": request.course_id,
                        "cohort_id": request.cohort_id
                    }
                )
                
                # Get the ID of the newly inserted enrollment for audit log
                new_enrollment_id = result.lastrowid
                
                # Insert Audit Log
                db.execute(
                    text("""
                        INSERT INTO enrollment_audit_log (enrollment_id, user_id, course_id, cohort_id, action, old_status, new_status, performed_by, performed_at)
                        VALUES (:eid, :uid, :cid, :cohort_id, 'bulk_enroll', NULL, 'pending-verification', :performed_by, CURRENT_TIMESTAMP)
                    """),
                    {
                        "eid": new_enrollment_id,
                        "uid": student_id,
                        "cid": request.course_id,
                        "cohort_id": request.cohort_id,
                        "performed_by": current_user_id
                    }
                )
                
                created_count += 1

            except Exception as e:
                # Catch any specific DB errors during insert (e.g., race conditions though unlikely in single thread sim)
                failed_list.append(FailedEnrollment(user_id=student_id, error=str(e)))

        db.commit()
        
        return BulkEnrollResponse(
            enrollments_created=created_count,
            failed_enrollments=failed_list
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
