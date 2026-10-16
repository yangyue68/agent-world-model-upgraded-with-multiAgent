import os
import sqlite3
from datetime import datetime, date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Database setup
DATABASE_URL = os.getenv("DATABASE_PATH")
if not DATABASE_URL:
    raise ValueError("DATABASE_PATH environment variable not set")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    code = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    applicant_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=False, default="Draft")
    submitted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class RecommendationRequirement(Base):
    __tablename__ = "recommendation_requirements"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    requirement_type = Column(String, nullable=False)
    required_count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("requirement_type IN ('Science Faculty','Clinical Supervisor','Other')"),
        UniqueConstraint("program_id", "requirement_type"),
    )

class RecommendationRequest(Base):
    __tablename__ = "recommendation_requests"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    requirement_id = Column(Integer, ForeignKey("recommendation_requirements.id"), nullable=False)
    recommender_name = Column(String, nullable=False)
    recommender_email = Column(String, nullable=False)
    recommender_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="Pending")
    requested_at = Column(DateTime, default=datetime.utcnow)
    submitted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("recommender_type IN ('Science Faculty','Clinical Supervisor','Other')"),
        CheckConstraint("status IN ('Pending','Submitted','Incomplete','Expired')"),
    )

class DeadlineRule(Base):
    __tablename__ = "deadline_rules"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    rule_name = Column(String, nullable=False)
    deadline_type = Column(String, nullable=False)
    deadline_date = Column(DateTime, nullable=False)
    action_on_missing = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("deadline_type IN ('Recommendation Submission','Final Application','Other')"),
        CheckConstraint("action_on_missing IN ('Waitlist Review','Reject','Notify')"),
    )

class ReminderSchedule(Base):
    __tablename__ = "reminder_schedules"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    reminder_type = Column(String, nullable=False)
    interval_days = Column(Integer, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("reminder_type IN ('Recommendation Incomplete','Application Incomplete','Other')"),
        CheckConstraint("interval_days > 0"),
        CheckConstraint("is_active IN (0,1)"),
    )

class ReminderLog(Base):
    __tablename__ = "reminder_logs"
    id = Column(Integer, primary_key=True, index=True)
    reminder_schedule_id = Column(Integer, ForeignKey("reminder_schedules.id"), nullable=False)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, nullable=False, default="Sent")
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('Sent','Failed','Skipped')"),
    )

# Request models
class LookupProgramsQuery(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None

class CreateRecommendationRequirementRequest(BaseModel):
    requirement_type: str
    required_count: int

class CreateDeadlineRuleRequest(BaseModel):
    rule_name: str
    deadline_type: str
    deadline_date: str
    action_on_missing: str

class CreateReminderScheduleRequest(BaseModel):
    reminder_type: str
    interval_days: int
    start_date: str
    end_date: Optional[str] = None

# Response models
class ProgramResponse(BaseModel):
    id: int
    name: str
    code: str

class RecommendationRequirementResponse(BaseModel):
    id: int
    program_id: int
    requirement_type: str
    required_count: int

class DeadlineRuleResponse(BaseModel):
    id: int
    program_id: int
    rule_name: str
    deadline_type: str
    deadline_date: str
    action_on_missing: str

class ReminderScheduleResponse(BaseModel):
    id: int
    program_id: int
    reminder_type: str
    interval_days: int
    start_date: str
    end_date: Optional[str] = None
    is_active: int

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/programs/lookup", response_model=List[ProgramResponse], operation_id="lookup_programs")
def lookup_programs(name: Optional[str] = None, code: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Program)
    if name:
        query = query.filter(Program.name.contains(name))
    if code:
        query = query.filter(Program.code == code)
    return query.all()

@app.post("/programs/{program_id}/recommendation-requirements", response_model=RecommendationRequirementResponse, operation_id="create_recommendation_requirement")
def create_recommendation_requirement(
    program_id: int,
    req: CreateRecommendationRequirementRequest,
    db: Session = Depends(get_db)
):
    # Check if program exists
    program = db.query(Program).filter(Program.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    
    # Check if requirement already exists for this type
    existing_req = db.query(RecommendationRequirement).filter(
        RecommendationRequirement.program_id == program_id,
        RecommendationRequirement.requirement_type == req.requirement_type
    ).first()
    
    if existing_req:
        # Update existing requirement
        existing_req.required_count = req.required_count
        existing_req.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing_req)
        return existing_req
    
    # Create new requirement
    new_req = RecommendationRequirement(
        program_id=program_id,
        requirement_type=req.requirement_type,
        required_count=req.required_count
    )
    db.add(new_req)
    db.commit()
    db.refresh(new_req)
    return new_req

@app.post("/programs/{program_id}/deadline-rules", response_model=DeadlineRuleResponse, operation_id="create_deadline_rule")
def create_deadline_rule(
    program_id: int,
    req: CreateDeadlineRuleRequest,
    db: Session = Depends(get_db)
):
    # Check if program exists
    program = db.query(Program).filter(Program.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    
    # Parse deadline date
    deadline_date = datetime.fromisoformat(req.deadline_date.replace('Z', '+00:00'))
    
    # Create new deadline rule
    new_rule = DeadlineRule(
        program_id=program_id,
        rule_name=req.rule_name,
        deadline_type=req.deadline_type,
        deadline_date=deadline_date,
        action_on_missing=req.action_on_missing
    )
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)
    return new_rule

@app.post("/programs/{program_id}/reminder-schedules", response_model=ReminderScheduleResponse, operation_id="create_reminder_schedule")
def create_reminder_schedule(
    program_id: int,
    req: CreateReminderScheduleRequest,
    db: Session = Depends(get_db)
):
    # Check if program exists
    program = db.query(Program).filter(Program.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    
    # Parse dates
    start_date = datetime.fromisoformat(req.start_date.replace('Z', '+00:00'))
    end_date = None
    if req.end_date:
        end_date = datetime.fromisoformat(req.end_date.replace('Z', '+00:00'))
    
    # Create new reminder schedule
    new_schedule = ReminderSchedule(
        program_id=program_id,
        reminder_type=req.reminder_type,
        interval_days=req.interval_days,
        start_date=start_date,
        end_date=end_date,
        is_active=1
    )
    db.add(new_schedule)
    db.commit()
    db.refresh(new_schedule)
    return new_schedule

# Configure MD Program 2026 workflow
@app.on_event("startup")
def configure_md_program_workflow():
    pass
