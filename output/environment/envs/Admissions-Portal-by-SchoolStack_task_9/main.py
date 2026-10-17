import os
from datetime import datetime
from typing import Literal
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, REAL, ForeignKey, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from fastapi import FastAPI, HTTPException, Path, Body, Depends
from pydantic import BaseModel
from typing import Optional

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    term = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Applicant(Base):
    __tablename__ = "applicants"
    id = Column(String, primary_key=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    date_of_birth = Column(String)
    email = Column(String)
    phone = Column(String)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String)
    file_size = Column(Integer)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class TestScore(Base):
    __tablename__ = "test_scores"
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    test_name = Column(String, nullable=False)
    score = Column(REAL)
    test_date = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class RecommendationRequest(Base):
    __tablename__ = "recommendation_requests"
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    recommender_name = Column(String, nullable=False)
    recommender_email = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    requested_at = Column(DateTime, default=datetime.utcnow)
    submitted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class SubmissionResponse(Base):
    __tablename__ = "submission_responses"
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    question_key = Column(String, nullable=False)
    response_text = Column(Text)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class MergeLog(Base):
    __tablename__ = "merge_logs"
    id = Column(Integer, primary_key=True)
    primary_applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    duplicate_applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    reviewer_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    merged_at = Column(DateTime, default=datetime.utcnow)
    details = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class ApplicantLookupResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    status: Literal["active", "merged", "deleted"]
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

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/applicants/lookup", response_model=list[ApplicantLookupResponse], operation_id="lookup_applicants_by_name_and_program")
def lookup_applicants_by_name_and_program(
    first_name: str,
    last_name: str,
    program_name: str,
    program_term: str,
    db: Session = Depends(get_db)
):
    program = db.query(Program).filter_by(name=program_name, term=program_term).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    applicants = db.query(Applicant).filter(
        Applicant.program_id == program.id,
        Applicant.first_name == first_name,
        Applicant.last_name == last_name
    ).all()

    return [
        ApplicantLookupResponse(
            id=applicant.id,
            first_name=applicant.first_name,
            last_name=applicant.last_name,
            date_of_birth=applicant.date_of_birth,
            email=applicant.email,
            phone=applicant.phone,
            status=applicant.status,
            program_id=applicant.program_id
        ) for applicant in applicants
    ]

@app.post("/applicants/{primary_id}/merge/{duplicate_id}", response_model=MergeDuplicateApplicantsResponse, operation_id="merge_duplicate_applicants")
def merge_duplicate_applicants(
    primary_id: str = Path(..., description="The ID of the primary applicant record to keep"),
    duplicate_id: str = Path(..., description="The ID of the duplicate applicant record to merge and remove"),
    request: MergeDuplicateApplicantsRequest = Body(...),
    db: Session = Depends(get_db)
):
    reviewer_user_id = request.reviewer_user_id
    
    # Validate reviewer exists
    reviewer = db.query(User).filter_by(id=reviewer_user_id).first()
    if not reviewer:
        raise HTTPException(status_code=404, detail="Reviewer user not found")

    # Get primary applicant
    primary_applicant = db.query(Applicant).filter_by(id=primary_id).first()
    if not primary_applicant:
        raise HTTPException(status_code=404, detail="Primary applicant not found")

    # Get duplicate applicant
    duplicate_applicant = db.query(Applicant).filter_by(id=duplicate_id).first()
    if not duplicate_applicant:
        raise HTTPException(status_code=404, detail="Duplicate applicant not found")

    # Ensure both applicants belong to the same program
    if primary_applicant.program_id != duplicate_applicant.program_id:
        raise HTTPException(status_code=400, detail="Applicants must belong to the same program")

    # Count items to be moved
    documents_count = db.query(Document).filter_by(applicant_id=duplicate_id).count()
    test_scores_count = db.query(TestScore).filter_by(applicant_id=duplicate_id).count()
    recommendation_requests_count = db.query(RecommendationRequest).filter_by(applicant_id=duplicate_id).count()
    submission_responses_count = db.query(SubmissionResponse).filter_by(applicant_id=duplicate_id).count()

    # Move documents
    db.query(Document).filter_by(applicant_id=duplicate_id).update({"applicant_id": primary_id})

    # Move test scores
    db.query(TestScore).filter_by(applicant_id=duplicate_id).update({"applicant_id": primary_id})

    # Move recommendation requests
    db.query(RecommendationRequest).filter_by(applicant_id=duplicate_id).update({"applicant_id": primary_id})

    # Move submission responses
    db.query(SubmissionResponse).filter_by(applicant_id=duplicate_id).update({"applicant_id": primary_id})

    # Resolve date of birth conflict - keep the one from primary applicant
    if primary_applicant.date_of_birth:
        duplicate_applicant.date_of_birth = primary_applicant.date_of_birth

    # Update the duplicate applicant's status to 'merged'
    duplicate_applicant.status = "merged"
    duplicate_applicant.updated_at = datetime.utcnow()

    # Create merge log entry
    merge_log = MergeLog(
        primary_applicant_id=primary_id,
        duplicate_applicant_id=duplicate_id,
        reviewer_user_id=reviewer_user_id,
        details=f"Merged duplicate applicant {duplicate_id} into primary applicant {primary_id}"
    )
    db.add(merge_log)
    db.flush()  # Get the ID before committing

    # Commit all changes
    db.commit()

    return MergeDuplicateApplicantsResponse(
        success=True,
        message=f"Successfully merged duplicate applicant {duplicate_id} into primary applicant {primary_id}",
        primary_applicant_id=primary_id,
        merged_from_applicant_id=duplicate_id,
        moved_documents_count=documents_count,
        moved_test_scores_count=test_scores_count,
        moved_recommendation_requests_count=recommendation_requests_count,
        moved_submission_responses_count=submission_responses_count,
        merge_log_id=merge_log.id
    )

if __name__ == "__main__":
    pass
