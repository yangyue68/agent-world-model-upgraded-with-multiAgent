import os
from datetime import date, timedelta
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Date,
    Text,
    DateTime,
    ForeignKey,
    create_engine,
    select,
    case,
    func,
)
from sqlalchemy.orm import Session, declarative_base, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, connect_args={"check_same_thread": False})
Base = declarative_base()


# --- SQLAlchemy Models ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())
    updated_at = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())


class Candidate(Base):
    __tablename__ = "candidates"
    id = Column(Integer, primary_key=True)
    candidate_code = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())
    updated_at = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())


class Certification(Base):
    __tablename__ = "certifications"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    passing_score = Column(Float, default=70.0)
    max_retakes = Column(Integer, default=2)
    retake_wait_days = Column(Integer, default=30)
    created_at = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())
    updated_at = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())


class TestCenter(Base):
    __tablename__ = "test_centers"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    country = Column(String)
    created_at = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())
    updated_at = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())


class ExamAttempt(Base):
    __tablename__ = "exam_attempts"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    certification_id = Column(Integer, ForeignKey("certifications.id", ondelete="RESTRICT"), nullable=False)
    test_center_id = Column(Integer, ForeignKey("test_centers.id", ondelete="SET NULL"))
    attempt_date = Column(Date, nullable=False)
    score = Column(Float, nullable=False)
    result = Column(String, nullable=False)
    attempt_number = Column(Integer, default=1, nullable=False)
    retake_eligible = Column(Integer, default=0, nullable=False)
    retake_available_date = Column(Date)
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), default=1, nullable=False)
    created_at = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())
    updated_at = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())

    candidate = relationship("Candidate")
    certification = relationship("Certification")
    test_center = relationship("TestCenter")


# --- Pydantic Schemas ---

class ExamAttemptRequest(BaseModel):
    candidate_id: int
    certification_id: int
    test_center_id: int
    attempt_date: str  # ISO-8601 date string
    score: float
    result: Literal["pass", "fail", "pending"]

class ExamAttemptResponse(BaseModel):
    id: int
    candidate_id: int
    certification_id: int
    test_center_id: int
    attempt_date: str
    score: float
    result: str
    attempt_number: int
    retake_eligible: int
    retake_available_date: Optional[str] = None

    class Config:
        from_attributes = True


# --- App Setup ---

app = FastAPI(title="CertChain API", version="1.0.0")


def parse_date(date_str: str) -> date:
    """Parse ISO-8601 date string, handling both 'T...Z' and space-separated forms."""
    try:
        return date.fromisoformat(date_str.replace('Z', '+00:00').split('T')[0])
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")


@app.post("/exam-attempts", operation_id="record_exam_attempt", response_model=ExamAttemptResponse)
def record_exam_attempt(attempt_data: ExamAttemptRequest):
    """Record a new exam attempt for a candidate"""
    
    # Parse attempt date
    attempt_date_obj = parse_date(attempt_data.attempt_date)
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    
    with Session(engine) as session:
        # Verify candidate exists
        candidate = session.execute(
            select(Candidate).where(Candidate.id == attempt_data.candidate_id)
        ).scalar_one_or_none()
        
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
            
        # Verify certification exists
        certification = session.execute(
            select(Certification).where(Certification.id == attempt_data.certification_id)
        ).scalar_one_or_none()
        
        if not certification:
            raise HTTPException(status_code=404, detail="Certification not found")
            
        # Verify test center exists
        test_center = session.execute(
            select(TestCenter).where(TestCenter.id == attempt_data.test_center_id)
        ).scalar_one_or_none()
        
        if not test_center:
            raise HTTPException(status_code=404, detail="Test center not found")
            
        # Calculate attempt number for this candidate/certification pair
        existing_attempts_count = session.execute(
            select(func.count()).select_from(ExamAttempt).where(
                ExamAttempt.candidate_id == attempt_data.candidate_id,
                ExamAttempt.certification_id == attempt_data.certification_id
            )
        ).scalar()
        
        next_attempt_number = existing_attempts_count + 1
        
        # Determine retake eligibility based on policy
        retake_eligible = 0
        retake_available_date = None
        
        if attempt_data.result == "fail":
            # Check if max retakes reached
            if next_attempt_number <= certification.max_retakes:
                retake_eligible = 1
                # Calculate available date based on wait days
                retake_available_date = attempt_date_obj + timedelta(days=certification.retake_wait_days)
            else:
                retake_eligible = 0
                retake_available_date = None
        elif attempt_data.result == "pass":
            retake_eligible = 0
            retake_available_date = None
            
        # Create new exam attempt record
        new_attempt = ExamAttempt(
            candidate_id=attempt_data.candidate_id,
            certification_id=attempt_data.certification_id,
            test_center_id=attempt_data.test_center_id,
            attempt_date=attempt_date_obj,
            score=attempt_data.score,
            result=attempt_data.result,
            attempt_number=next_attempt_number,
            retake_eligible=retake_eligible,
            retake_available_date=retake_available_date,
            created_by=current_user_id
        )
        
        session.add(new_attempt)
        session.commit()
        session.refresh(new_attempt)
        
        # Return response
        return ExamAttemptResponse(
            id=new_attempt.id,
            candidate_id=new_attempt.candidate_id,
            certification_id=new_attempt.certification_id,
            test_center_id=new_attempt.test_center_id,
            attempt_date=new_attempt.attempt_date.isoformat(),
            score=new_attempt.score,
            result=new_attempt.result,
            attempt_number=new_attempt.attempt_number,
            retake_eligible=new_attempt.retake_eligible,
            retake_available_date=new_attempt.retake_available_date.isoformat() if new_attempt.retake_available_date else None
        )
