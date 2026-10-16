import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH", "sqlite:///./test.db")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Intake(Base):
    __tablename__ = "intakes"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    term = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    program = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("term IN ('Fall', 'Spring', 'Summer', 'Winter')", name="check_term"),
        CheckConstraint("year > 0", name="check_year"),
        CheckConstraint("program IS NOT NULL", name="check_program"),
    )

class Applicant(Base):
    __tablename__ = "applicants"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    intake_id = Column(Integer, ForeignKey("intakes.id"), nullable=False)
    gpa = Column(Float, nullable=False)
    decision_flag = Column(String, nullable=False)
    submission_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User")
    intake = relationship("Intake")
    
    __table_args__ = (
        CheckConstraint("gpa >= 0.0 AND gpa <= 4.0", name="check_gpa"),
        CheckConstraint("decision_flag IN ('Pending', 'Accepted', 'Rejected', 'Waitlist')", name="check_decision_flag"),
    )

class Interview(Base):
    __tablename__ = "interviews"
    
    id = Column(Integer, primary_key=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="Scheduled")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    applicant = relationship("Applicant")
    
    __table_args__ = (
        CheckConstraint("status IN ('Scheduled', 'Completed', 'Cancelled', 'No Show')", name="check_status"),
    )

# Pydantic Models
class IntakeResponse(BaseModel):
    id: int
    name: str
    term: str
    year: int
    program: str

class ApplicantResponse(BaseModel):
    applicant_id: int
    user_id: int
    gpa: float
    decision_flag: str
    submission_date: datetime
    full_name: str
    email: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/intakes/lookup", response_model=IntakeResponse, operation_id="lookup_intake_by_term_year_program")
def lookup_intake_by_term_year_program(
    term: str,
    year: int,
    program: str,
    db = None
):
    if db is None:
        db = next(get_db())
    
    intake = db.query(Intake).filter(
        Intake.term == term,
        Intake.year == year,
        Intake.program == program
    ).first()
    
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found")
    
    return intake

@app.get("/applicants/filter", response_model=List[ApplicantResponse], operation_id="filter_applicants")
def filter_applicants(
    intake_id: int,
    min_gpa: Optional[float] = Query(None),
    decision_flag: Optional[str] = Query(None),
    has_scheduled_interview: Optional[bool] = Query(None),
    db = None
):
    if db is None:
        db = next(get_db())
    
    query = db.query(Applicant).join(User).filter(Applicant.intake_id == intake_id)
    
    # Apply GPA filter if provided
    if min_gpa is not None:
        query = query.filter(Applicant.gpa >= min_gpa)
    
    # Apply decision flag filter if provided
    if decision_flag is not None:
        query = query.filter(Applicant.decision_flag == decision_flag)
    
    # Apply scheduled interview filter if provided
    if has_scheduled_interview is True:
        subquery = db.query(Interview.applicant_id).filter(
            Interview.status == "Scheduled"
        ).subquery()
        query = query.join(subquery, Applicant.id == subquery.c.applicant_id)
    
    # Sort by submission date descending
    query = query.order_by(Applicant.submission_date.desc())
    
    applicants = query.all()
    
    # Format response
    result = []
    for applicant in applicants:
        result.append({
            "applicant_id": applicant.id,
            "user_id": applicant.user_id,
            "gpa": applicant.gpa,
            "decision_flag": applicant.decision_flag,
            "submission_date": applicant.submission_date,
            "full_name": applicant.user.full_name,
            "email": applicant.user.email
        })
    
    return result
