import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, UniqueConstraint, TEXT, REAL
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = "sqlite:///./test.db"
# Remove the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH.replace('sqlite:///', '')
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Request and Response Models
class LookupApplicantsRequest(BaseModel):
    first_name: str
    last_name: str
    program_name: str
    program_term: str

class ApplicantResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: str
    program_id: int

class MergeDuplicateApplicantsRequest(BaseModel):
    reviewer_user_id: int

class MergeDuplicateApplicantsResponse(BaseModel):
    success: bool
    message: str
    primary_applicant_id: str
    merged_from_applicant_id: str
    moved_documents_count: int
    moved_test_scores_count: int
    moved_recommendation_requests_count: int
    moved_submission_responses_count: int
    merge_log_id: int

# Helper functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def validate_applicant_exists(db, applicant_id: str):
    result = db.execute(text("SELECT id FROM applicants WHERE id = :id"), {"id": applicant_id}).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail=f"Applicant with ID {applicant_id} not found")

def validate_program_exists(db, program_name: str, program_term: str):
    result = db.execute(
        text("SELECT id FROM programs WHERE name = :name AND term = :term"),
        {"name": program_name, "term": program_term}
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Program not found")

def validate_user_exists(db, user_id: int):
    result = db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id}).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found")

def get_applicant_info(db, applicant_id: str):
    result = db.execute(
        text("""
            SELECT id, first_name, last_name, date_of_birth, email, phone, status, program_id
            FROM applicants WHERE id = :id
        """),
        {"id": applicant_id}
    ).fetchone()
    return result

def get_applicant_documents_count(db, applicant_id: str):
    result = db.execute(
        text("SELECT COUNT(*) FROM documents WHERE applicant_id = :id"),
        {"id": applicant_id}
    ).fetchone()
    return result[0]

def get_applicant_test_scores_count(db, applicant_id: str):
    result = db.execute(
        text("SELECT COUNT(*) FROM test_scores WHERE applicant_id = :id"),
        {"id": applicant_id}
    ).fetchone()
    return result[0]

def get_applicant_recommendation_requests_count(db, applicant_id: str):
    result = db.execute(
        text("SELECT COUNT(*) FROM recommendation_requests WHERE applicant_id = :id"),
        {"id": applicant_id}
    ).fetchone()
    return result[0]

def get_applicant_submission_responses_count(db, applicant_id: str):
    result = db.execute(
        text("SELECT COUNT(*) FROM submission_responses WHERE applicant_id = :id"),
        {"id": applicant_id}
    ).fetchone()
    return result[0]

def update_applicant_status(db, applicant_id: str, status: str):
    db.execute(
        text("UPDATE applicants SET status = :status WHERE id = :id"),
        {"status": status, "id": applicant_id}
    )
    db.commit()

def move_documents(db, primary_id: str, duplicate_id: str):
    # Update documents to point to primary applicant
    db.execute(
        text("UPDATE documents SET applicant_id = :primary_id WHERE applicant_id = :duplicate_id"),
        {"primary_id": primary_id, "duplicate_id": duplicate_id}
    )
    db.commit()
    return get_applicant_documents_count(db, primary_id)

def move_test_scores(db, primary_id: str, duplicate_id: str):
    # Update test scores to point to primary applicant
    db.execute(
        text("UPDATE test_scores SET applicant_id = :primary_id WHERE applicant_id = :duplicate_id"),
        {"primary_id": primary_id, "duplicate_id": duplicate_id}
    )
    db.commit()
    return get_applicant_test_scores_count(db, primary_id)

def move_recommendation_requests(db, primary_id: str, duplicate_id: str):
    # Update recommendation requests to point to primary applicant
    db.execute(
        text("UPDATE recommendation_requests SET applicant_id = :primary_id WHERE applicant_id = :duplicate_id"),
        {"primary_id": primary_id, "duplicate_id": duplicate_id}
    )
    db.commit()
    return get_applicant_recommendation_requests_count(db, primary_id)

def move_submission_responses(db, primary_id: str, duplicate_id: str):
    # Update submission responses to point to primary applicant
    db.execute(
        text("UPDATE submission_responses SET applicant_id = :primary_id WHERE applicant_id = :duplicate_id"),
        {"primary_id": primary_id, "duplicate_id": duplicate_id}
    )
    db.commit()
    return get_applicant_submission_responses_count(db, primary_id)

def log_merge_action(db, primary_id: str, duplicate_id: str, reviewer_user_id: int):
    # Insert merge log entry
    db.execute(
        text("""
            INSERT INTO merge_logs (primary_applicant_id, duplicate_applicant_id, reviewer_user_id, merged_at)
            VALUES (:primary_id, :duplicate_id, :reviewer_user_id, :merged_at)
        """),
        {
            "primary_id": primary_id,
            "duplicate_id": duplicate_id,
            "reviewer_user_id": reviewer_user_id,
            "merged_at": datetime.utcnow()
        }
    )
    db.commit()
    
    # Get the ID of the inserted log entry
    result = db.execute(
        text("SELECT last_insert_rowid()"),
    ).fetchone()
    return result[0]

# Endpoints
@app.get("/applicants/lookup", response_model=List[ApplicantResponse], operation_id="lookup_applicants_by_name_and_program")
def lookup_applicants_by_name_and_program(
    first_name: str = Query(...),
    last_name: str = Query(...),
    program_name: str = Query(...),
    program_term: str = Query(...),
    db = Depends(get_db)
):
    # Validate program exists
    validate_program_exists(db, program_name, program_term)
    
    # Find applicants matching criteria
    result = db.execute(
        text("""
            SELECT a.id, a.first_name, a.last_name, a.date_of_birth, a.email, a.phone, a.status, a.program_id
            FROM applicants a
            JOIN programs p ON a.program_id = p.id
            WHERE a.first_name = :first_name
            AND a.last_name = :last_name
            AND p.name = :program_name
            AND p.term = :program_term
        """),
        {
            "first_name": first_name,
            "last_name": last_name,
            "program_name": program_name,
            "program_term": program_term
        }
    ).fetchall()
    
    return [
        ApplicantResponse(
            id=row[0],
            first_name=row[1],
            last_name=row[2],
            date_of_birth=row[3],
            email=row[4],
            phone=row[5],
            status=row[6],
            program_id=row[7]
        ) for row in result
    ]

@app.post("/applicants/{primary_id}/merge/{duplicate_id}", response_model=MergeDuplicateApplicantsResponse, operation_id="merge_duplicate_applicants")
def merge_duplicate_applicants(
    primary_id: str = Path(...),
    duplicate_id: str = Path(...),
    body: MergeDuplicateApplicantsRequest = None,
    db = Depends(get_db)
):
    # Validate both applicants exist
    validate_applicant_exists(db, primary_id)
    validate_applicant_exists(db, duplicate_id)
    
    # Validate reviewer user exists
    validate_user_exists(db, body.reviewer_user_id)
    
    # Ensure primary and duplicate are different
    if primary_id == duplicate_id:
        raise HTTPException(status_code=400, detail="Primary and duplicate applicant IDs must be different")
    
    # Get applicant info
    primary_info = get_applicant_info(db, primary_id)
    duplicate_info = get_applicant_info(db, duplicate_id)
    
    # Verify they're in the same program
    if primary_info.program_id != duplicate_info.program_id:
        raise HTTPException(status_code=400, detail="Applicants must be in the same program")
    
    # Resolve conflicting date_of_birth - keep from primary
    new_dob = primary_info.date_of_birth
    
    # Update duplicate applicant's DOB to match primary
    db.execute(
        text("UPDATE applicants SET date_of_birth = :dob WHERE id = :id"),
        {"dob": new_dob, "id": duplicate_id}
    )
    db.commit()
    
    # Move all associated data from duplicate to primary
    moved_documents_count = move_documents(db, primary_id, duplicate_id)
    moved_test_scores_count = move_test_scores(db, primary_id, duplicate_id)
    moved_recommendation_requests_count = move_recommendation_requests(db, primary_id, duplicate_id)
    moved_submission_responses_count = move_submission_responses(db, primary_id, duplicate_id)
    
    # Delete the duplicate applicant
    db.execute(text("DELETE FROM applicants WHERE id = :id"), {"id": duplicate_id})
    db.commit()
    
    # Log the merge action
    merge_log_id = log_merge_action(db, primary_id, duplicate_id, body.reviewer_user_id)
    
    return MergeDuplicateApplicantsResponse(
        success=True,
        message=f"Merged duplicate applicant {duplicate_id} into primary applicant {primary_id}",
        primary_applicant_id=primary_id,
        merged_from_applicant_id=duplicate_id,
        moved_documents_count=moved_documents_count,
        moved_test_scores_count=moved_test_scores_count,
        moved_recommendation_requests_count=moved_recommendation_requests_count,
        moved_submission_responses_count=moved_submission_responses_count,
        merge_log_id=merge_log_id
    )
