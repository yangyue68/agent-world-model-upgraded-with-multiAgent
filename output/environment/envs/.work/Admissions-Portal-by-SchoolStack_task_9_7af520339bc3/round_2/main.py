import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text, select, update, delete
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.getenv("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is required")

# Strip the 'sqlite:///' prefix to get the file path
db_file_path = DATABASE_PATH.replace("sqlite:///", "")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Request and Response Models
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

# Helper functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def validate_applicant_exists(db, applicant_id: str):
    stmt = select(text("COUNT(*)")).select_from(text("applicants")).where(text("id = :id"))
    result = db.execute(stmt, {"id": applicant_id}).fetchone()
    if result[0] == 0:
        raise HTTPException(status_code=404, detail=f"Applicant {applicant_id} not found")

def validate_program_exists(db, program_name: str, program_term: str):
    stmt = select(text("id")).select_from(text("programs")).where(
        text("name = :name AND term = :term")
    )
    result = db.execute(stmt, {"name": program_name, "term": program_term}).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Program not found")

def validate_user_exists(db, user_id: int):
    stmt = select(text("COUNT(*)")).select_from(text("users")).where(text("id = :id"))
    result = db.execute(stmt, {"id": user_id}).fetchone()
    if result[0] == 0:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

def validate_applicant_is_active(db, applicant_id: str):
    stmt = select(text("status")).select_from(text("applicants")).where(text("id = :id"))
    result = db.execute(stmt, {"id": applicant_id}).fetchone()
    if not result or result[0] != "active":
        raise HTTPException(status_code=400, detail=f"Applicant {applicant_id} is not active")

# Endpoints
@app.get("/applicants/lookup", response_model=List[Applicant])
def lookup_applicants_by_name_and_program(
    first_name: str = Query(...),
    last_name: str = Query(...),
    program_name: str = Query(...),
    program_term: str = Query(...),
    db: SessionLocal = Depends(get_db)
):
    # Validate program exists
    validate_program_exists(db, program_name, program_term)
    
    # Get program id
    stmt = select(text("id")).select_from(text("programs")).where(
        text("name = :name AND term = :term")
    )
    program_result = db.execute(stmt, {"name": program_name, "term": program_term}).fetchone()
    program_id = program_result[0]
    
    # Find applicants matching criteria
    stmt = select(text("*")).select_from(text("applicants")).where(
        text("first_name = :first_name AND last_name = :last_name AND program_id = :program_id")
    )
    result = db.execute(stmt, {
        "first_name": first_name,
        "last_name": last_name,
        "program_id": program_id
    }).fetchall()
    
    return [Applicant(**dict(row)) for row in result]

@app.post("/applicants/{primary_id}/merge/{duplicate_id}", response_model=MergeResponse)
def merge_duplicate_applicants(
    primary_id: str = Path(...),
    duplicate_id: str = Path(...),
    merge_request: MergeRequest,
    db: SessionLocal = Depends(get_db)
):
    # Validate both applicants exist
    validate_applicant_exists(db, primary_id)
    validate_applicant_exists(db, duplicate_id)
    
    # Validate both applicants are active
    validate_applicant_is_active(db, primary_id)
    validate_applicant_is_active(db, duplicate_id)
    
    # Validate reviewer user exists
    validate_user_exists(db, merge_request.reviewer_user_id)
    
    # Check if they're the same applicant
    if primary_id == duplicate_id:
        raise HTTPException(status_code=400, detail="Primary and duplicate applicant IDs cannot be the same")
    
    # Begin transaction
    try:
        # Count items to be moved
        docs_count = db.execute(select(text("COUNT(*)")).select_from(text("documents")).where(text("applicant_id = :id")), {"id": duplicate_id}).fetchone()[0]
        test_scores_count = db.execute(select(text("COUNT(*)")).select_from(text("test_scores")).where(text("applicant_id = :id")), {"id": duplicate_id}).fetchone()[0]
        rec_reqs_count = db.execute(select(text("COUNT(*)")).select_from(text("recommendation_requests")).where(text("applicant_id = :id")), {"id": duplicate_id}).fetchone()[0]
        sub_resp_count = db.execute(select(text("COUNT(*)")).select_from(text("submission_responses")).where(text("applicant_id = :id")), {"id": duplicate_id}).fetchone()[0]
        
        # Move documents
        stmt = update(text("documents")).where(text("applicant_id = :old_id")).values(applicant_id=primary_id)
        db.execute(stmt, {"old_id": duplicate_id})
        
        # Move test scores
        stmt = update(text("test_scores")).where(text("applicant_id = :old_id")).values(applicant_id=primary_id)
        db.execute(stmt, {"old_id": duplicate_id})
        
        # Move recommendation requests
        stmt = update(text("recommendation_requests")).where(text("applicant_id = :old_id")).values(applicant_id=primary_id)
        db.execute(stmt, {"old_id": duplicate_id})
        
        # Move submission responses
        stmt = update(text("submission_responses")).where(text("applicant_id = :old_id")).values(applicant_id=primary_id)
        db.execute(stmt, {"old_id": duplicate_id})
        
        # Resolve conflicting date_of_birth - keep from primary
        primary_dob = db.execute(select(text("date_of_birth")).select_from(text("applicants")).where(text("id = :id")), {"id": primary_id}).fetchone()[0]
        duplicate_dob = db.execute(select(text("date_of_birth")).select_from(text("applicants")).where(text("id = :id")), {"id": duplicate_id}).fetchone()[0]
        
        # Update primary applicant with primary DOB (already correct)
        # No need to change anything since we're keeping primary's DOB
        
        # Delete duplicate applicant
        stmt = delete(text("applicants")).where(text("id = :id"))
        db.execute(stmt, {"id": duplicate_id})
        
        # Log the merge
        merge_log_stmt = text("""
            INSERT INTO merge_logs 
            (primary_applicant_id, duplicate_applicant_id, reviewer_user_id, merged_at) 
            VALUES (:primary_id, :duplicate_id, :reviewer_id, :merged_at)
        """)
        result = db.execute(merge_log_stmt, {
            "primary_id": primary_id,
            "duplicate_id": duplicate_id,
            "reviewer_id": merge_request.reviewer_user_id,
            "merged_at": datetime.now().isoformat()
        })
        merge_log_id = result.lastrowid
        
        # Commit transaction
        db.commit()
        
        return MergeResponse(
            success=True,
            message=f"Merged duplicate applicant {duplicate_id} into primary applicant {primary_id}",
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
        raise HTTPException(status_code=500, detail=f"Error merging applicants: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
