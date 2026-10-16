import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = "sqlite:////tmp/test.db"
DB_FILE_PATH = DATABASE_PATH.replace("sqlite:///", "")
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
            FROM applicants 
            WHERE id = :id
        """),
        {"id": applicant_id}
    ).fetchone()
    return result

def get_applicant_documents(db, applicant_id: str):
    result = db.execute(
        text("SELECT * FROM documents WHERE applicant_id = :id"),
        {"id": applicant_id}
    ).fetchall()
    return result

def get_applicant_test_scores(db, applicant_id: str):
    result = db.execute(
        text("SELECT * FROM test_scores WHERE applicant_id = :id"),
        {"id": applicant_id}
    ).fetchall()
    return result

def get_applicant_recommendations(db, applicant_id: str):
    result = db.execute(
        text("SELECT * FROM recommendation_requests WHERE applicant_id = :id"),
        {"id": applicant_id}
    ).fetchall()
    return result

def get_applicant_submissions(db, applicant_id: str):
    result = db.execute(
        text("SELECT * FROM submission_responses WHERE applicant_id = :id"),
        {"id": applicant_id}
    ).fetchall()
    return result

# Endpoints
@app.get("/applicants/lookup", response_model=List[Applicant], operation_id="lookup_applicants_by_name_and_program")
def lookup_applicants_by_name_and_program(
    first_name: str = Query(...),
    last_name: str = Query(...),
    program_name: str = Query(...),
    program_term: str = Query(...),
    db = next(get_db())
):
    validate_program_exists(db, program_name, program_term)
    
    # Get program ID
    program_result = db.execute(
        text("SELECT id FROM programs WHERE name = :name AND term = :term"),
        {"name": program_name, "term": program_term}
    ).fetchone()
    program_id = program_result.id
    
    # Find applicants
    applicants = db.execute(
        text("""
            SELECT id, first_name, last_name, date_of_birth, email, phone, status, program_id
            FROM applicants 
            WHERE first_name = :first_name 
            AND last_name = :last_name 
            AND program_id = :program_id
        """),
        {
            "first_name": first_name,
            "last_name": last_name,
            "program_id": program_id
        }
    ).fetchall()
    
    return [Applicant(**row._asdict()) for row in applicants]

@app.post("/applicants/{primary_id}/merge/{duplicate_id}", response_model=MergeResponse, operation_id="merge_duplicate_applicants")
def merge_duplicate_applicants(
    primary_id: str = Path(...),
    duplicate_id: str = Path(...),
    merge_request: MergeRequest,
    db = next(get_db())
):
    reviewer_user_id = merge_request.reviewer_user_id
    
    # Validate inputs
    validate_applicant_exists(db, primary_id)
    validate_applicant_exists(db, duplicate_id)
    validate_user_exists(db, reviewer_user_id)
    
    # Verify they're different applicants
    if primary_id == duplicate_id:
        raise HTTPException(status_code=400, detail="Primary and duplicate applicant IDs must be different")
    
    # Get applicant info
    primary_applicant = get_applicant_info(db, primary_id)
    duplicate_applicant = get_applicant_info(db, duplicate_id)
    
    # Verify both applicants are in the same program
    if primary_applicant.program_id != duplicate_applicant.program_id:
        raise HTTPException(status_code=400, detail="Applicants must be in the same program")
    
    # Verify primary applicant is active
    if primary_applicant.status != "active":
        raise HTTPException(status_code=400, detail="Primary applicant must be active")
    
    # Verify duplicate applicant is active
    if duplicate_applicant.status != "active":
        raise HTTPException(status_code=400, detail="Duplicate applicant must be active")
    
    # Count items to be moved
    documents = get_applicant_documents(db, duplicate_id)
    test_scores = get_applicant_test_scores(db, duplicate_id)
    recommendations = get_applicant_recommendations(db, duplicate_id)
    submissions = get_applicant_submissions(db, duplicate_id)
    
    # Begin transaction
    try:
        # Move documents
        for doc in documents:
            db.execute(
                text("UPDATE documents SET applicant_id = :new_id WHERE id = :doc_id"),
                {"new_id": primary_id, "doc_id": doc.id}
            )
        
        # Move test scores
        for score in test_scores:
            db.execute(
                text("UPDATE test_scores SET applicant_id = :new_id WHERE id = :score_id"),
                {"new_id": primary_id, "score_id": score.id}
            )
        
        # Move recommendation requests
        for rec in recommendations:
            db.execute(
                text("UPDATE recommendation_requests SET applicant_id = :new_id WHERE id = :rec_id"),
                {"new_id": primary_id, "rec_id": rec.id}
            )
        
        # Move submission responses
        for sub in submissions:
            db.execute(
                text("UPDATE submission_responses SET applicant_id = :new_id WHERE id = :sub_id"),
                {"new_id": primary_id, "sub_id": sub.id}
            )
        
        # Resolve date of birth conflict - keep primary's date of birth
        # Update the primary applicant's date of birth if needed
        if duplicate_applicant.date_of_birth != primary_applicant.date_of_birth:
            db.execute(
                text("UPDATE applicants SET date_of_birth = :dob WHERE id = :id"),
                {"dob": primary_applicant.date_of_birth, "id": primary_id}
            )
        
        # Mark duplicate applicant as merged
        db.execute(
            text("UPDATE applicants SET status = 'merged' WHERE id = :id"),
            {"id": duplicate_id}
        )
        
        # Log the merge
        merge_log_result = db.execute(
            text("""
                INSERT INTO merge_logs (
                    primary_applicant_id, 
                    duplicate_applicant_id, 
                    reviewer_user_id, 
                    merged_at
                ) VALUES (:primary_id, :duplicate_id, :reviewer_id, :merged_at)
                RETURNING id
            """),
            {
                "primary_id": primary_id,
                "duplicate_id": duplicate_id,
                "reviewer_id": reviewer_user_id,
                "merged_at": datetime.utcnow().isoformat() + "Z"
            }
        )
        merge_log_id = merge_log_result.fetchone()[0]
        
        # Commit transaction
        db.commit()
        
        return MergeResponse(
            success=True,
            message=f"Merged duplicate applicant {duplicate_id} into primary applicant {primary_id}",
            primary_applicant_id=primary_id,
            merged_from_applicant_id=duplicate_id,
            moved_documents_count=len(documents),
            moved_test_scores_count=len(test_scores),
            moved_recommendation_requests_count=len(recommendations),
            moved_submission_responses_count=len(submissions),
            merge_log_id=merge_log_id
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error during merge: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
