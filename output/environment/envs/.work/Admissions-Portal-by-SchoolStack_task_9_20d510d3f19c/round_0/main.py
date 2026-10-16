import os
from typing import List, Optional
from datetime import datetime
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, UniqueConstraint, Text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import IntegrityError

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_URL")
if not DATABASE_PATH:
    raise ValueError("DATABASE_URL environment variable not set")

# Remove the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH.replace('sqlite:///', '')
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# FastAPI app
app = FastAPI()

# Models for request/response
class Applicant(BaseModel):
    id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: str
    program_id: int

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

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Helper function to check if applicant exists
def get_applicant(db: Session, applicant_id: str):
    return db.execute(text("SELECT * FROM applicants WHERE id = :id"), {"id": applicant_id}).fetchone()

# Helper function to check if user exists
def get_user(db: Session, user_id: int):
    return db.execute(text("SELECT * FROM users WHERE id = :id"), {"id": user_id}).fetchone()

# Endpoint 1: Lookup applicants by name and program details
@app.get("/applicants/lookup", response_model=List[Applicant])
def lookup_applicants_by_name_and_program(
    first_name: str,
    last_name: str,
    program_name: str,
    program_term: str,
    db: Session = Depends(get_db)
):
    result = db.execute(text("""
        SELECT a.id, a.first_name, a.last_name, a.date_of_birth, a.email, a.phone, a.status, a.program_id
        FROM applicants a
        JOIN programs p ON a.program_id = p.id
        WHERE a.first_name = :first_name 
        AND a.last_name = :last_name
        AND p.name = :program_name
        AND p.term = :program_term
    """), {
        "first_name": first_name,
        "last_name": last_name,
        "program_name": program_name,
        "program_term": program_term
    }).fetchall()
    
    return [Applicant(**row._asdict()) for row in result]

# Endpoint 2: Merge duplicate applicant records
@app.post("/applicants/{primary_id}/merge/{duplicate_id}", response_model=MergeResponse)
def merge_duplicate_applicants(
    primary_id: str,
    duplicate_id: str,
    reviewer_user_id: int,
    db: Session = Depends(get_db)
):
    # Validate primary applicant exists
    primary_applicant = get_applicant(db, primary_id)
    if not primary_applicant:
        raise HTTPException(status_code=404, detail="Primary applicant not found")
    
    # Validate duplicate applicant exists
    duplicate_applicant = get_applicant(db, duplicate_id)
    if not duplicate_applicant:
        raise HTTPException(status_code=404, detail="Duplicate applicant not found")
    
    # Validate reviewer user exists
    reviewer = get_user(db, reviewer_user_id)
    if not reviewer:
        raise HTTPException(status_code=404, detail="Reviewer user not found")
    
    # Ensure both applicants are in the same program
    if primary_applicant.program_id != duplicate_applicant.program_id:
        raise HTTPException(status_code=400, detail="Applicants must be in the same program")
    
    # Begin transaction
    try:
        # Count items to be moved
        docs_count = db.execute(text("SELECT COUNT(*) FROM documents WHERE applicant_id = :id"), {"id": duplicate_id}).scalar()
        test_scores_count = db.execute(text("SELECT COUNT(*) FROM test_scores WHERE applicant_id = :id"), {"id": duplicate_id}).scalar()
        rec_reqs_count = db.execute(text("SELECT COUNT(*) FROM recommendation_requests WHERE applicant_id = :id"), {"id": duplicate_id}).scalar()
        sub_resp_count = db.execute(text("SELECT COUNT(*) FROM submission_responses WHERE applicant_id = :id"), {"id": duplicate_id}).scalar()
        
        # Move documents
        db.execute(text("""
            UPDATE documents SET applicant_id = :primary_id 
            WHERE applicant_id = :duplicate_id
        """), {"primary_id": primary_id, "duplicate_id": duplicate_id})
        
        # Move test scores
        db.execute(text("""
            UPDATE test_scores SET applicant_id = :primary_id 
            WHERE applicant_id = :duplicate_id
        """), {"primary_id": primary_id, "duplicate_id": duplicate_id})
        
        # Move recommendation requests
        db.execute(text("""
            UPDATE recommendation_requests SET applicant_id = :primary_id 
            WHERE applicant_id = :duplicate_id
        """), {"primary_id": primary_id, "duplicate_id": duplicate_id})
        
        # Move submission responses
        db.execute(text("""
            UPDATE submission_responses SET applicant_id = :primary_id 
            WHERE applicant_id = :duplicate_id
        """), {"primary_id": primary_id, "duplicate_id": duplicate_id})
        
        # Resolve conflicting date_of_birth - keep the one from primary
        # Update primary applicant's DOB if needed (but don't change it if it's already correct)
        # In this case, we're keeping the primary's DOB as-is
        
        # Delete the duplicate applicant
        db.execute(text("DELETE FROM applicants WHERE id = :id"), {"id": duplicate_id})
        
        # Log the merge
        merge_log_result = db.execute(text("""
            INSERT INTO merge_logs (
                primary_applicant_id, 
                duplicate_applicant_id, 
                reviewer_user_id, 
                merged_at
            ) VALUES (:primary_id, :duplicate_id, :reviewer_id, :merged_at)
            RETURNING id
        """), {
            "primary_id": primary_id,
            "duplicate_id": duplicate_id,
            "reviewer_id": reviewer_user_id,
            "merged_at": datetime.now()
        })
        
        merge_log_id = merge_log_result.fetchone()[0]
        
        # Commit transaction
        db.commit()
        
        return MergeResponse(
            success=True,
            message=f"Successfully merged applicant {duplicate_id} into {primary_id}",
            primary_applicant_id=primary_id,
            merged_from_applicant_id=duplicate_id,
            moved_documents_count=docs_count,
            moved_test_scores_count=test_scores_count,
            moved_recommendation_requests_count=rec_reqs_count,
            moved_submission_responses_count=sub_resp_count,
            merge_log_id=merge_log_id
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to merge applicants: {str(e)}")
