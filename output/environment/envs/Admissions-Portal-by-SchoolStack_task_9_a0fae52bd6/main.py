import os
from datetime import datetime
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Path, Query, Depends
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    sql,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

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
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())


class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    term = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    applicants = relationship("Applicant", back_populates="program")


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
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    program = relationship("Program", back_populates="applicants")
    documents = relationship("Document", back_populates="applicant")
    test_scores = relationship("TestScore", back_populates="applicant")
    recommendation_requests = relationship("RecommendationRequest", back_populates="applicant")
    submission_responses = relationship("SubmissionResponse", back_populates="applicant")


class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    file_name = Column(String, nullable=False)
    file_path = Column(Text, nullable=False)
    file_type = Column(String)
    file_size = Column(Integer)
    uploaded_at = Column(DateTime, default=func.current_timestamp())
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    applicant = relationship("Applicant", back_populates="documents")


class TestScore(Base):
    __tablename__ = "test_scores"
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    test_name = Column(String, nullable=False)
    score = Column(sql.sqltypes.REAL)
    test_date = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    applicant = relationship("Applicant", back_populates="test_scores")


class RecommendationRequest(Base):
    __tablename__ = "recommendation_requests"
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    recommender_name = Column(String, nullable=False)
    recommender_email = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    requested_at = Column(DateTime, default=func.current_timestamp())
    submitted_at = Column(DateTime)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    applicant = relationship("Applicant", back_populates="recommendation_requests")


class SubmissionResponse(Base):
    __tablename__ = "submission_responses"
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    question_key = Column(String, nullable=False)
    response_text = Column(Text)
    submitted_at = Column(DateTime, default=func.current_timestamp())
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    applicant = relationship("Applicant", back_populates="submission_responses")


class MergeLog(Base):
    __tablename__ = "merge_logs"
    id = Column(Integer, primary_key=True)
    primary_applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    duplicate_applicant_id = Column(String, ForeignKey("applicants.id"), nullable=False)
    reviewer_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    merged_at = Column(DateTime, default=func.current_timestamp())
    details = Column(Text)
    created_at = Column(DateTime, default=func.current_timestamp())


# Pydantic Models

class ApplicantResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    status: Literal["active", "merged", "deleted"]
    program_id: int


class MergeRequestBody(BaseModel):
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


# FastAPI App

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/applicants/lookup", response_model=list[ApplicantResponse], operation_id="lookup_applicants_by_name_and_program")
def lookup_applicants_by_name_and_program(
    first_name: str = Query(...),
    last_name: str = Query(...),
    program_name: str = Query(...),
    program_term: str = Query(...),
    db=Depends(get_db),
):
    # Find the program
    program = db.query(Program).filter(
        Program.name == program_name,
        Program.term == program_term
    ).first()

    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Find applicants
    applicants = db.query(Applicant).filter(
        Applicant.program_id == program.id,
        Applicant.first_name == first_name,
        Applicant.last_name == last_name
    ).all()

    return [
        ApplicantResponse(
            id=applicant.id,
            first_name=applicant.first_name,
            last_name=applicant.last_name,
            date_of_birth=applicant.date_of_birth,
            email=applicant.email,
            phone=applicant.phone,
            status=applicant.status,
            program_id=applicant.program_id
        )
        for applicant in applicants
    ]


@app.post("/applicants/{primary_id}/merge/{duplicate_id}", response_model=MergeResponse, operation_id="merge_duplicate_applicants")
def merge_duplicate_applicants(
    primary_id: str = Path(...),
    duplicate_id: str = Path(...),
    body: MergeRequestBody = None,
    db=Depends(get_db),
):
    reviewer_user_id = body.reviewer_user_id if body else None

    # Validate reviewer exists
    reviewer = db.query(User).filter(User.id == reviewer_user_id).first()
    if not reviewer:
        raise HTTPException(status_code=404, detail="Reviewer user not found")

    # Get primary applicant
    primary_applicant = db.query(Applicant).filter(Applicant.id == primary_id).first()
    if not primary_applicant:
        raise HTTPException(status_code=404, detail="Primary applicant not found")

    # Get duplicate applicant
    duplicate_applicant = db.query(Applicant).filter(Applicant.id == duplicate_id).first()
    if not duplicate_applicant:
        raise HTTPException(status_code=404, detail="Duplicate applicant not found")

    # Ensure they're the same program
    if primary_applicant.program_id != duplicate_applicant.program_id:
        raise HTTPException(status_code=400, detail="Applicants must belong to the same program")

    # Move documents
    documents = db.query(Document).filter(Document.applicant_id == duplicate_id).all()
    moved_documents_count = len(documents)
    for doc in documents:
        doc.applicant_id = primary_id

    # Move test scores
    test_scores = db.query(TestScore).filter(TestScore.applicant_id == duplicate_id).all()
    moved_test_scores_count = len(test_scores)
    for ts in test_scores:
        ts.applicant_id = primary_id

    # Move recommendation requests
    rec_reqs = db.query(RecommendationRequest).filter(RecommendationRequest.applicant_id == duplicate_id).all()
    moved_recommendation_requests_count = len(rec_reqs)
    for req in rec_reqs:
        req.applicant_id = primary_id

    # Move submission responses
    sub_resps = db.query(SubmissionResponse).filter(SubmissionResponse.applicant_id == duplicate_id).all()
    moved_submission_responses_count = len(sub_resps)
    for resp in sub_resps:
        resp.applicant_id = primary_id

    # Resolve date of birth conflict - keep primary's date of birth
    if primary_applicant.date_of_birth and duplicate_applicant.date_of_birth:
        # Keep the primary's date of birth
        pass  # Already set correctly

    # Update the duplicate applicant's status to 'merged'
    duplicate_applicant.status = "merged"

    # Create merge log entry
    merge_log = MergeLog(
        primary_applicant_id=primary_id,
        duplicate_applicant_id=duplicate_id,
        reviewer_user_id=reviewer_user_id,
        details=f"Merged duplicate applicant {duplicate_id} into primary applicant {primary_id}"
    )
    db.add(merge_log)
    db.flush()  # Get the ID without committing

    # Commit all changes
    db.commit()

    return MergeResponse(
        success=True,
        message=f"Successfully merged duplicate applicant {duplicate_id} into primary applicant {primary_id}",
        primary_applicant_id=primary_id,
        merged_from_applicant_id=duplicate_id,
        moved_documents_count=moved_documents_count,
        moved_test_scores_count=moved_test_scores_count,
        moved_recommendation_requests_count=moved_recommendation_requests_count,
        moved_submission_responses_count=moved_submission_responses_count,
        merge_log_id=merge_log.id
    )
