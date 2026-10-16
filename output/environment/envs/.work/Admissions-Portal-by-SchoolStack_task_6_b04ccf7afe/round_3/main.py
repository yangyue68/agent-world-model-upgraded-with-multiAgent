import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    code = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    applicant_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=False, default="Draft")
    submitted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class RecommendationRequirement(Base):
    __tablename__ = "recommendation_requirements"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    requirement_type = Column(String, nullable=False)
    required_count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("requirement_type IN ('Science Faculty','Clinical Supervisor','Other')", name="chk_rec_req_type"),
        CheckConstraint("required_count > 0", name="chk_rec_req_count"),
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
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("recommender_type IN ('Science Faculty','Clinical Supervisor','Other')", name="chk_rec_req_type"),
        CheckConstraint("status IN ('Pending','Submitted','Incomplete','Expired')", name="chk_rec_req_status"),
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
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("deadline_type IN ('Recommendation Submission','Final Application','Other')", name="chk_deadline_type"),
        CheckConstraint("action_on_missing IN ('Waitlist Review','Reject','Notify')", name="chk_action_missing"),
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
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("reminder_type IN ('Recommendation Incomplete','Application Incomplete','Other')", name="chk_reminder_type"),
        CheckConstraint("interval_days > 0", name="chk_interval_days"),
        CheckConstraint("is_active IN (0,1)", name="chk_is_active"),
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
        CheckConstraint("status IN ('Sent','Failed','Skipped')", name="chk_reminder_log_status"),
    )

# Pydantic Models
class ProgramLookupResponse(BaseModel):
    id: int
    name: str
    code: str

class RecommendationRequirementCreate(BaseModel):
    requirement_type: str
    required_count: int

class RecommendationRequirementResponse(BaseModel):
    id: int
    program_id: int
    requirement_type: str
    required_count: int

class DeadlineRuleCreate(BaseModel):
    rule_name: str
    deadline_type: str
    deadline_date: str
    action_on_missing: str

class DeadlineRuleResponse(BaseModel):
    id: int
    program_id: int
    rule_name: str
    deadline_type: str
    deadline_date: str
    action_on_missing: str

class ReminderScheduleCreate(BaseModel):
    reminder_type: str
    interval_days: int
    start_date: str
    end_date: Optional[str] = None

class ReminderScheduleResponse(BaseModel):
    id: int
    program_id: int
    reminder_type: str
    interval_days: int
    start_date: str
    end_date: Optional[str] = None
    is_active: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/programs/lookup", response_model=List[ProgramLookupResponse], operation_id="lookup_programs")
def lookup_programs(name: Optional[str] = None, code: Optional[str] = None, db=None):
    if db is None:
        db = next(get_db())
    
    query = db.query(Program)
    if name:
        query = query.filter(Program.name.contains(name))
    if code:
        query = query.filter(Program.code == code)
    
    return query.all()

@app.post("/programs/{program_id}/recommendation-requirements", response_model=RecommendationRequirementResponse, operation_id="create_recommendation_requirement")
def create_recommendation_requirement(
    program_id: int,
    requirement: RecommendationRequirementCreate,
    db=None
):
    if db is None:
        db = next(get_db())
    
    # Check if a requirement already exists for this type
    existing = db.query(RecommendationRequirement).filter(
        RecommendationRequirement.program_id == program_id,
        RecommendationRequirement.requirement_type == requirement.requirement_type
    ).first()
    
    if existing:
        existing.required_count = requirement.required_count
        db.commit()
        db.refresh(existing)
        return existing
    
    new_requirement = RecommendationRequirement(
        program_id=program_id,
        requirement_type=requirement.requirement_type,
        required_count=requirement.required_count
    )
    db.add(new_requirement)
    db.commit()
    db.refresh(new_requirement)
    return new_requirement

@app.post("/programs/{program_id}/deadline-rules", response_model=DeadlineRuleResponse, operation_id="create_deadline_rule")
def create_deadline_rule(
    program_id: int,
    rule: DeadlineRuleCreate,
    db=None
):
    if db is None:
        db = next(get_db())
    
    new_rule = DeadlineRule(
        program_id=program_id,
        rule_name=rule.rule_name,
        deadline_type=rule.deadline_type,
        deadline_date=datetime.fromisoformat(rule.deadline_date.replace('Z', '+00:00')),
        action_on_missing=rule.action_on_missing
    )
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)
    return new_rule

@app.post("/programs/{program_id}/reminder-schedules", response_model=ReminderScheduleResponse, operation_id="create_reminder_schedule")
def create_reminder_schedule(
    program_id: int,
    reminder: ReminderScheduleCreate,
    db=None
):
    if db is None:
        db = next(get_db())
    
    new_reminder = ReminderSchedule(
        program_id=program_id,
        reminder_type=reminder.reminder_type,
        interval_days=reminder.interval_days,
        start_date=datetime.fromisoformat(reminder.start_date.replace('Z', '+00:00')),
        end_date=datetime.fromisoformat(reminder.end_date.replace('Z', '+00:00')) if reminder.end_date else None
    )
    db.add(new_reminder)
    db.commit()
    db.refresh(new_reminder)
    return new_reminder

# Configure MD Program 2026
@app.on_event("startup")
def configure_md_program():
    db = next(get_db())
    
    # Find the MD Program 2026
    md_program = db.query(Program).filter(Program.code == "MD-2026").first()
    if not md_program:
        raise HTTPException(status_code=404, detail="MD Program 2026 not found")
    
    # Create recommendation requirements
    create_recommendation_requirement(md_program.id, RecommendationRequirementCreate(
        requirement_type="Science Faculty",
        required_count=2
    ), db)
    
    create_recommendation_requirement(md_program.id, RecommendationRequirementCreate(
        requirement_type="Clinical Supervisor",
        required_count=1
    ), db)
    
    # Create deadline rules
    create_deadline_rule(md_program.id, DeadlineRuleCreate(
        rule_name="MD Rec Deadline",
        deadline_type="Recommendation Submission",
        deadline_date="2025-12-15T23:59:00Z",
        action_on_missing="Waitlist Review"
    ), db)
    
    create_deadline_rule(md_program.id, DeadlineRuleCreate(
        rule_name="MD Final Application Deadline",
        deadline_type="Final Application",
        deadline_date="2026-01-10T23:59:00Z",
        action_on_missing="Waitlist Review"
    ), db)
    
    # Create reminder schedules
    create_reminder_schedule(md_program.id, ReminderScheduleCreate(
        reminder_type="Recommendation Incomplete",
        interval_days=7,
        start_date="2025-12-01T00:00:00Z",
        end_date="2025-12-15T23:59:00Z"
    ), db)
