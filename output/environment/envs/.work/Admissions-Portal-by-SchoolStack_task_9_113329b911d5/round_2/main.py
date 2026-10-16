import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Read DATABASE_PATH at module import; never choose a fallback.
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Admissions Portal by SchoolStack")


# ---------- Pydantic Models ----------

class ApplicantLookupResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: str
    program_id: int


class MergeRequest(BaseModel):
    reviewer_user_id: int = Field(..., description="The ID of the user performing the merge, used for audit logging")


class MergeResponse(BaseModel):
    success: bool
    message: str
    primary_applicant_id: str
    merged_from_applicant_id: str
    moved_documents_count: int = 0
    moved_test_scores_count: int = 0
    moved_recommendation_requests_count: int = 0
    moved_submission_responses_count: int = 0
    merge_log_id: Optional[int] = None


# ---------- Endpoints ----------

@app.get("/applicants/lookup", operation_id="lookup_applicants_by_name_and_program", response_model=List[ApplicantLookupResponse], tags=["Applicants"])
def lookup_applicants_by_name_and_program(
    first_name: str = Query(..., description="First name of the applicant"),
    last_name: str = Query(..., description="Last name of the applicant"),
    program_name: str = Query(..., description="Name of the program (e.g., 'Data Science')"),
    program_term: str = Query(..., description="Term of the program (e.g., 'Fall 2025')"),
):
    """Find applicant records matching the provided first name, last name, and program name/term."""
    session = SessionLocal()
    try:
        # First find the program_id from programs table
        prog_result = session.execute(
            text("SELECT id FROM programs WHERE name = :name AND term = :term"),
            {"name": program_name, "term": program_term}
        ).fetchone()
        if prog_result is None:
            return []
        program_id = prog_result[0]

        rows = session.execute(
            text("""
                SELECT id, first_name, last_name, date_of_birth, email, phone, status, program_id
                FROM applicants
                WHERE first_name = :first_name
                  AND last_name = :last_name
                  AND program_id = :program_id
            """),
            {"first_name": first_name, "last_name": last_name, "program_id": program_id}
        ).fetchall()

        results = []
        for row in rows:
            results.append(ApplicantLookupResponse(
                id=row.id,
                first_name=row.first_name,
                last_name=row.last_name,
                date_of_birth=row.date_of_birth,
                email=row.email,
                phone=row.phone,
                status=row.status,
                program_id=row.program_id,
            ))
        return results
    finally:
        session.close()


@app.post("/applicants/{primary_id}/merge/{duplicate_id}", operation_id="merge_duplicate_applicants", response_model=MergeResponse, tags=["Applicants"])
def merge_duplicate_applicants(
    primary_id: str,
    duplicate_id: str,
    body: MergeRequest = Body(...),
):
    """Merge duplicate applicant records into primary record."""
    reviewer_user_id = body.reviewer_user_id

    session = SessionLocal()
    try:
        # Validate that both applicants exist
        primary_applicant = session.execute(
            text("SELECT id, first_name, last_name, date_of_birth, email, phone, status, program_id FROM applicants WHERE id = :id"),
            {"id": primary_id}
        ).fetchone()
        if primary_applicant is None:
            raise HTTPException(status_code=404, detail=f"Primary applicant '{primary_id}' not found")

        duplicate_applicant = session.execute(
            text("SELECT id, first_name, last_name, date_of_birth, email, phone, status, program_id FROM applicants WHERE id = :id"),
            {"id": duplicate_id}
        ).fetchone()
        if duplicate_applicant is None:
            raise HTTPException(status_code=404, detail=f"Duplicate applicant '{duplicate_id}' not found")

        # Validate reviewer user exists
        reviewer = session.execute(
            text("SELECT id FROM users WHERE id = :id"),
            {"id": reviewer_user_id}
        ).fetchone()
        if reviewer is None:
            raise HTTPException(status_code=404, detail=f"Reviewer user with ID {reviewer_user_id} not found")

        # Count items to be moved before moving them
        doc_count = session.execute(
            text("SELECT COUNT(*) FROM documents WHERE applicant_id = :aid"),
            {"aid": duplicate_id}
        ).scalar() or 0

        test_score_count = session.execute(
            text("SELECT COUNT(*) FROM test_scores WHERE applicant_id = :aid"),
            {"aid": duplicate_id}
        ).scalar() or 0

        rec_req_count = session.execute(
            text("SELECT COUNT(*) FROM recommendation_requests WHERE applicant_id = :aid"),
            {"aid": duplicate_id}
        ).scalar() or 0

        sub_resp_count = session.execute(
            text("SELECT COUNT(*) FROM submission_responses WHERE applicant_id = :aid"),
            {"aid": duplicate_id}
        ).scalar() or 0

        # Move all associated data from duplicate to primary
        session.execute(
            text("UPDATE documents SET applicant_id = :primary_id WHERE applicant_id = :duplicate_id"),
            {"primary_id": primary_id, "duplicate_id": duplicate_id}
        )
        session.execute(
            text("UPDATE test_scores SET applicant_id = :primary_id WHERE applicant_id = :duplicate_id"),
            {"primary_id": primary_id, "duplicate_id": duplicate_id}
        )
        session.execute(
            text("UPDATE recommendation_requests SET applicant_id = :primary_id WHERE applicant_id = :duplicate_id"),
            {"primary_id": primary_id, "duplicate_id": duplicate_id}
        )
        session.execute(
            text("UPDATE submission_responses SET applicant_id = :primary_id WHERE applicant_id = :duplicate_id"),
            {"primary_id": primary_id, "duplicate_id": duplicate_id}
        )

        # Resolve conflicting personal information: keep values from primary (no change needed since we're keeping primary's DOB etc.)
        # The primary record already has its own date_of_birth, so we just leave it as-is.

        # Mark the duplicate as 'merged' instead of deleting it, because merge_logs has
        # FOREIGN KEY (duplicate_applicant_id) REFERENCES applicants(id) ON DELETE RESTRICT,
        # which prevents physical deletion while the audit log references it.
        session.execute(
            text("UPDATE applicants SET status = 'merged', updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"id": duplicate_id}
        )

        # Log the merge action
        merge_log_result = session.execute(
            text("""
                INSERT INTO merge_logs (primary_applicant_id, duplicate_applicant_id, reviewer_user_id, merged_at, details)
                VALUES (:primary_id, :duplicate_id, :reviewer_id, CURRENT_TIMESTAMP, :details)
            """),
            {
                "primary_id": primary_id,
                "duplicate_id": duplicate_id,
                "reviewer_id": reviewer_user_id,
                "details": f"Merged duplicate {duplicate_id} into primary {primary_id}. Moved {doc_count} documents, {test_score_count} test scores, {rec_req_count} recommendation requests, {sub_resp_count} submission responses."
            }
        )
        merge_log_id = merge_log_result.lastrowid

        session.commit()

        return MergeResponse(
            success=True,
            message=f"Successfully merged applicant {duplicate_id} into {primary_id}",
            primary_applicant_id=primary_id,
            merged_from_applicant_id=duplicate_id,
            moved_documents_count=doc_count,
            moved_test_scores_count=test_score_count,
            moved_recommendation_requests_count=rec_req_count,
            moved_submission_responses_count=sub_resp_count,
            merge_log_id=merge_log_id,
        )
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()
