import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, UniqueConstraint, Text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

# Database setup
DATABASE_PATH = "sqlite:////tmp/test.db"
# Remove the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH.replace('sqlite:///', '')
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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
    return True

def validate_program_exists(db, program_name: str, program_term: str):
    result = db.execute(
        text("SELECT id FROM programs WHERE name = :name AND term = :term"),
        {"name": program_name, "term": program_term}
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Program not found")
    return result.id

def validate_user_exists(db, user_id: int):
    result = db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id}).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found")
    return True

def get_applicant_info(db, applicant_id: str):
    result = db.execute(
        text("""
            SELECT id, first_name, last_name, date_of_birth, email, phone, status, program_id 
            FROM applicants 
            WHERE id = :id
        """),
        {"id": applicant_id}
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail=f"Applicant with ID {applicant_id} not found")
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

def update_applicant_field(db, applicant_id: str, field: str, value):
    db.execute(
        text(f"UPDATE applicants SET {field} = :value WHERE id = :id"),
        {"value": value, "id": applicant_id}
    )
    db.commit()

def move_documents(db, from_applicant_id: str, to_applicant_id: str):
    documents = get_applicant_documents(db, from_applicant_id)
    count = 0
    for doc in documents:
        db.execute(
            text("""
                UPDATE documents 
                SET applicant_id = :to_applicant_id 
                WHERE id = :doc_id
            """),
            {"to_applicant_id": to_applicant_id, "doc_id": doc.id}
        )
        count += 1
    db.commit()
    return count

def move_test_scores(db, from_applicant_id: str, to_applicant_id: str):
    scores = get_applicant_test_scores(db, from_applicant_id)
    count = 0
    for score in scores:
        db.execute(
            text("""
                UPDATE test_scores 
                SET applicant_id = :to_applicant_id 
                WHERE id = :score_id
            """),
            {"to_applicant_id": to_applicant_id, "score_id": score.id}
        )
        count += 1
    db.commit()
    return count

def move_recommendations(db, from_applicant_id: str, to_applicant_id: str):
    recommendations = get_applicant_recommendations(db, from_applicant_id)
    count = 0
    for rec in recommendations:
        db.execute(
            text("""
                UPDATE recommendation_requests 
                SET applicant_id = :to_applicant_id 
                WHERE id = :rec_id
            """),
            {"to_applicant_id": to_applicant_id, "rec_id": rec.id}
        )
        count += 1
    db.commit()
    return count

def move_submissions(db, from_applicant_id: str, to_applicant_id: str):
    submissions = get_applicant_submissions(db, from_applicant_id)
    count = 0
    for sub in submissions:
        db.execute(
            text("""
                UPDATE submission_responses 
                SET applicant_id = :to_applicant_id 
                WHERE id = :sub_id
            """),
            {"to_applicant_id": to_applicant_id, "sub_id": sub.id}
        )
        count += 1
    db.commit()
    return count

def log_merge(db, primary_id: str, duplicate_id: str, reviewer_id: int):
    result = db.execute(
        text("""
            INSERT INTO merge_logs (primary_applicant_id, duplicate_applicant_id, reviewer_user_id, merged_at)
            VALUES (:primary_id, :duplicate_id, :reviewer_id, :merged_at)
            RETURNING id
        """),
        {
            "primary_id": primary_id,
            "duplicate_id": duplicate_id,
            "reviewer_id": reviewer_id,
            "merged_at": datetime.utcnow()
        }
    ).fetchone()
    db.commit()
    return result.id

# API Endpoints
@app.get("/applicants/lookup", response_model=List[Applicant])
def lookup_applicants_by_name_and_program(
    first_name: str = Query(...),
    last_name: str = Query(...),
    program_name: str = Query(...),
    program_term: str = Query(...),
    db: SessionLocal = Depends(get_db)
):
    program_id = validate_program_exists(db, program_name, program_term)
    
    results = db.execute(
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
    
    return [
        Applicant(
            id=row.id,
            first_name=row.first_name,
            last_name=row.last_name,
            date_of_birth=row.date_of_birth,
            email=row.email,
            phone=row.phone,
            status=row.status,
            program_id=row.program_id
        ) for row in results
    ]

@app.post("/applicants/{primary_id}/merge/{duplicate_id}", response_model=MergeResponse)
def merge_duplicate_applicants(
    primary_id: str = Path(...),
    duplicate_id: str = Path(...),
    reviewer_user_id: int = Query(...),
    db: SessionLocal = Depends(get_db)
):
    # Validate that both applicants exist
    validate_applicant_exists(db, primary_id)
    validate_applicant_exists(db, duplicate_id)
    
    # Validate that reviewer exists
    validate_user_exists(db, reviewer_user_id)
    
    # Get applicant info
    primary_info = get_applicant_info(db, primary_id)
    duplicate_info = get_applicant_info(db, duplicate_id)
    
    # Check if they're the same person
    if (primary_info.first_name != duplicate_info.first_name or 
        primary_info.last_name != duplicate_info.last_name):
        raise HTTPException(status_code=400, detail="Applicants have different names")
    
    # Resolve conflicting date_of_birth - keep from primary
    if duplicate_info.date_of_birth != primary_info.date_of_birth:
        # Update the primary applicant's date_of_birth to match the primary one
        update_applicant_field(db, primary_id, "date_of_birth", primary_info.date_of_birth)
    
    # Move all associated data
    moved_documents_count = move_documents(db, duplicate_id, primary_id)
    moved_test_scores_count = move_test_scores(db, duplicate_id, primary_id)
    moved_recommendation_requests_count = move_recommendations(db, duplicate_id, primary_id)
    moved_submission_responses_count = move_submissions(db, duplicate_id, primary_id)
    
    # Delete the duplicate applicant
    db.execute(
        text("DELETE FROM applicants WHERE id = :id"),
        {"id": duplicate_id}
    )
    db.commit()
    
    # Log the merge
    merge_log_id = log_merge(db, primary_id, duplicate_id, reviewer_user_id)
    
    return MergeResponse(
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
