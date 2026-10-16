import os
import sqlite3
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is required")

# Strip the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH.replace("sqlite:///", "")

# Create SQLAlchemy engine
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Pydantic models
class Applicant(BaseModel):
    id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: str
    program_id: int

class MergeRequest(BaseModel):
    reviewer_user_id: int

class MergeResponse(BaseModel):
    success: bool
    message: str
    primary_applicant_id: str
    merged_from_applicant_id: str
    moved_documents_count: int
    moved_test_scores_count: int
    moved_recommendation_requests_count: int
    moved_submission_responses_count: int
    merge_log_id: int

# Helper function to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Endpoint 1: Lookup applicants by name and program details
@app.get("/applicants/lookup", response_model=List[Applicant], operation_id="lookup_applicants_by_name_and_program")
def lookup_applicants_by_name_and_program(
    first_name: str = Query(...),
    last_name: str = Query(...),
    program_name: str = Query(...),
    program_term: str = Query(...),
    db = next(get_db())
):
    # Get program_id first
    program_query = text("""
        SELECT id FROM programs 
        WHERE name = :program_name AND term = :program_term
    """)
    
    result = db.execute(program_query, {"program_name": program_name, "program_term": program_term})
    program_row = result.fetchone()
    
    if not program_row:
        raise HTTPException(status_code=404, detail="Program not found")
    
    program_id = program_row[0]
    
    # Find applicants matching criteria
    query = text("""
        SELECT id, first_name, last_name, date_of_birth, email, phone, status, program_id
        FROM applicants
        WHERE first_name = :first_name 
        AND last_name = :last_name 
        AND program_id = :program_id
    """)
    
    result = db.execute(query, {
        "first_name": first_name,
        "last_name": last_name,
        "program_id": program_id
    })
    
    applicants = []
    for row in result.fetchall():
        applicants.append(Applicant(
            id=row[0],
            first_name=row[1],
            last_name=row[2],
            date_of_birth=row[3],
            email=row[4],
            phone=row[5],
            status=row[6],
            program_id=row[7]
        ))
    
    return applicants

# Endpoint 2: Merge duplicate applicant records
@app.post("/applicants/{primary_id}/merge/{duplicate_id}", response_model=MergeResponse, operation_id="merge_duplicate_applicants")
def merge_duplicate_applicants(
    primary_id: str = Path(...),
    duplicate_id: str = Path(...),
    merge_request: MergeRequest,
    db = next(get_db())
):
    reviewer_user_id = merge_request.reviewer_user_id
    
    # Validate reviewer exists
    user_query = text("SELECT id FROM users WHERE id = :user_id")
    user_result = db.execute(user_query, {"user_id": reviewer_user_id})
    if not user_result.fetchone():
        raise HTTPException(status_code=404, detail="Reviewer user not found")
    
    # Validate primary applicant exists
    primary_query = text("SELECT id, status FROM applicants WHERE id = :applicant_id")
    primary_result = db.execute(primary_query, {"applicant_id": primary_id})
    primary_row = primary_result.fetchone()
    
    if not primary_row:
        raise HTTPException(status_code=404, detail="Primary applicant not found")
    
    if primary_row[1] != "active":
        raise HTTPException(status_code=400, detail="Primary applicant is not active")
    
    # Validate duplicate applicant exists
    duplicate_query = text("SELECT id, status FROM applicants WHERE id = :applicant_id")
    duplicate_result = db.execute(duplicate_query, {"applicant_id": duplicate_id})
    duplicate_row = duplicate_result.fetchone()
    
    if not duplicate_row:
        raise HTTPException(status_code=404, detail="Duplicate applicant not found")
    
    if duplicate_row[1] == "merged" or duplicate_row[1] == "deleted":
        raise HTTPException(status_code=400, detail="Duplicate applicant is already merged or deleted")
    
    # Check if they're in the same program
    check_program_query = text("""
        SELECT a1.program_id, a2.program_id
        FROM applicants a1, applicants a2
        WHERE a1.id = :primary_id AND a2.id = :duplicate_id
    """)
    
    program_check = db.execute(check_program_query, {"primary_id": primary_id, "duplicate_id": duplicate_id})
    program_rows = program_check.fetchone()
    
    if not program_rows or program_rows[0] != program_rows[1]:
        raise HTTPException(status_code=400, detail="Applicants must be in the same program")
    
    # Count related records before moving
    document_count_query = text("SELECT COUNT(*) FROM documents WHERE applicant_id = :applicant_id")
    test_score_count_query = text("SELECT COUNT(*) FROM test_scores WHERE applicant_id = :applicant_id")
    recommendation_request_count_query = text("SELECT COUNT(*) FROM recommendation_requests WHERE applicant_id = :applicant_id")
    submission_response_count_query = text("SELECT COUNT(*) FROM submission_responses WHERE applicant_id = :applicant_id")
    
    doc_count = db.execute(document_count_query, {"applicant_id": duplicate_id}).scalar()
    test_count = db.execute(test_score_count_query, {"applicant_id": duplicate_id}).scalar()
    rec_count = db.execute(recommendation_request_count_query, {"applicant_id": duplicate_id}).scalar()
    sub_count = db.execute(submission_response_count_query, {"applicant_id": duplicate_id}).scalar()
    
    # Begin transaction
    try:
        # Move documents
        update_docs_query = text("""
            UPDATE documents SET applicant_id = :primary_id 
            WHERE applicant_id = :duplicate_id
        """)
        db.execute(update_docs_query, {"primary_id": primary_id, "duplicate_id": duplicate_id})
        
        # Move test scores
        update_tests_query = text("""
            UPDATE test_scores SET applicant_id = :primary_id 
            WHERE applicant_id = :duplicate_id
        """)
        db.execute(update_tests_query, {"primary_id": primary_id, "duplicate_id": duplicate_id})
        
        # Move recommendation requests
        update_recs_query = text("""
            UPDATE recommendation_requests SET applicant_id = :primary_id 
            WHERE applicant_id = :duplicate_id
        """)
        db.execute(update_recs_query, {"primary_id": primary_id, "duplicate_id": duplicate_id})
        
        # Move submission responses
        update_subs_query = text("""
            UPDATE submission_responses SET applicant_id = :primary_id 
            WHERE applicant_id = :duplicate_id
        """)
        db.execute(update_subs_query, {"primary_id": primary_id, "duplicate_id": duplicate_id})
        
        # Resolve date of birth conflict - keep primary's date of birth
        # Update the duplicate applicant's record to match primary's DOB
        update_dob_query = text("""
            UPDATE applicants SET date_of_birth = (
                SELECT date_of_birth FROM applicants WHERE id = :primary_id
            ) WHERE id = :duplicate_id
        """)
        db.execute(update_dob_query, {"primary_id": primary_id, "duplicate_id": duplicate_id})
        
        # Mark duplicate applicant as merged
        update_duplicate_status_query = text("""
            UPDATE applicants SET status = 'merged' WHERE id = :duplicate_id
        """)
        db.execute(update_duplicate_status_query, {"duplicate_id": duplicate_id})
        
        # Log the merge
        log_merge_query = text("""
            INSERT INTO merge_logs (primary_applicant_id, duplicate_applicant_id, reviewer_user_id, merged_at)
            VALUES (:primary_id, :duplicate_id, :reviewer_id, :merged_at)
            RETURNING id
        """)
        
        result = db.execute(log_merge_query, {
            "primary_id": primary_id,
            "duplicate_id": duplicate_id,
            "reviewer_id": reviewer_user_id,
            "merged_at": datetime.now()
        })
        
        merge_log_id = result.scalar()
        
        # Commit transaction
        db.commit()
        
        return MergeResponse(
            success=True,
            message=f"Merged duplicate applicant {duplicate_id} into primary applicant {primary_id}",
            primary_applicant_id=primary_id,
            merged_from_applicant_id=duplicate_id,
            moved_documents_count=doc_count,
            moved_test_scores_count=test_count,
            moved_recommendation_requests_count=rec_count,
            moved_submission_responses_count=sub_count,
            merge_log_id=merge_log_id
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error during merge: {str(e)}")
