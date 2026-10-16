import os
import sqlite3
from datetime import datetime, date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, CheckConstraint, ForeignKey, Index, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Database setup
DATABASE_PATH = "sqlite:///./test.db"

# Remove the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH.replace('sqlite:///', '')

# Create engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models (using raw SQL instead of ORM for simplicity)
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
    description = Column(Text)
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

class ReminderLog(Base):
    __tablename__ = "reminder_logs"
    id = Column(Integer, primary_key=True, index=True)
    reminder_schedule_id = Column(Integer, ForeignKey("reminder_schedules.id"), nullable=False)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, nullable=False, default="Sent")
    created_at = Column(DateTime, default=datetime.utcnow)

# Request and Response Models
class LookupProgramsQuery(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None

class CreateRecommendationRequirementRequest(BaseModel):
    requirement_type: str
    required_count: int

class CreateDeadlineRuleRequest(BaseModel):
    rule_name: str
    deadline_type: str
    deadline_date: datetime
    action_on_missing: str

class CreateReminderScheduleRequest(BaseModel):
    reminder_type: str
    interval_days: int
    start_date: datetime
    end_date: Optional[datetime] = None

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Helper function to get program ID by name or code
def get_program_id(db: Session, name: Optional[str] = None, code: Optional[str] = None) -> int:
    query = db.query(Program.id).filter(Program.id == Program.id)
    if name:
        query = query.filter(Program.name.like(f"%{name}%"))
    if code:
        query = query.filter(Program.code == code)
    
    result = query.first()
    if not result:
        raise HTTPException(status_code=404, detail="Program not found")
    return result[0]

# Endpoints
@app.get("/programs/lookup", response_model=List[dict])
def lookup_programs(
    name: Optional[str] = None,
    code: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Program.id, Program.name, Program.code)
    if name:
        query = query.filter(Program.name.like(f"%{name}%"))
    if code:
        query = query.filter(Program.code == code)
    
    results = query.all()
    return [{"id": r[0], "name": r[1], "code": r[2]} for r in results]

@app.post("/programs/{program_id}/recommendation-requirements", response_model=dict)
def create_recommendation_requirement(
    program_id: int,
    req: CreateRecommendationRequirementRequest,
    db: Session = Depends(get_db)
):
    # Validate requirement type
    valid_types = ["Science Faculty", "Clinical Supervisor", "Other"]
    if req.requirement_type not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid requirement type")
    
    # Check if a requirement already exists for this type
    existing = db.query(RecommendationRequirement).filter(
        RecommendationRequirement.program_id == program_id,
        RecommendationRequirement.requirement_type == req.requirement_type
    ).first()
    
    if existing:
        # Update existing
        existing.required_count = req.required_count
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return {
            "id": existing.id,
            "program_id": existing.program_id,
            "requirement_type": existing.requirement_type,
            "required_count": existing.required_count
        }
    else:
        # Create new
        new_req = RecommendationRequirement(
            program_id=program_id,
            requirement_type=req.requirement_type,
            required_count=req.required_count
        )
        db.add(new_req)
        db.commit()
        db.refresh(new_req)
        return {
            "id": new_req.id,
            "program_id": new_req.program_id,
            "requirement_type": new_req.requirement_type,
            "required_count": new_req.required_count
        }

@app.post("/programs/{program_id}/deadline-rules", response_model=dict)
def create_deadline_rule(
    program_id: int,
    req: CreateDeadlineRuleRequest,
    db: Session = Depends(get_db)
):
    # Validate deadline type
    valid_deadline_types = ["Recommendation Submission", "Final Application", "Other"]
    if req.deadline_type not in valid_deadline_types:
        raise HTTPException(status_code=400, detail="Invalid deadline type")
    
    # Validate action
    valid_actions = ["Waitlist Review", "Reject", "Notify"]
    if req.action_on_missing not in valid_actions:
        raise HTTPException(status_code=400, detail="Invalid action on missing")
    
    # Create new rule
    new_rule = DeadlineRule(
        program_id=program_id,
        rule_name=req.rule_name,
        deadline_type=req.deadline_type,
        deadline_date=req.deadline_date,
        action_on_missing=req.action_on_missing
    )
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)
    return {
        "id": new_rule.id,
        "program_id": new_rule.program_id,
        "rule_name": new_rule.rule_name,
        "deadline_type": new_rule.deadline_type,
        "deadline_date": new_rule.deadline_date,
        "action_on_missing": new_rule.action_on_missing
    }

@app.post("/programs/{program_id}/reminder-schedules", response_model=dict)
def create_reminder_schedule(
    program_id: int,
    req: CreateReminderScheduleRequest,
    db: Session = Depends(get_db)
):
    # Validate reminder type
    valid_types = ["Recommendation Incomplete", "Application Incomplete", "Other"]
    if req.reminder_type not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid reminder type")
    
    # Create new schedule
    new_schedule = ReminderSchedule(
        program_id=program_id,
        reminder_type=req.reminder_type,
        interval_days=req.interval_days,
        start_date=req.start_date,
        end_date=req.end_date
    )
    db.add(new_schedule)
    db.commit()
    db.refresh(new_schedule)
    return {
        "id": new_schedule.id,
        "program_id": new_schedule.program_id,
        "reminder_type": new_schedule.reminder_type,
        "interval_days": new_schedule.interval_days,
        "start_date": new_schedule.start_date,
        "end_date": new_schedule.end_date,
        "is_active": new_schedule.is_active
    }

# Configure the MD Program 2026 according to the task
@app.on_event("startup")
def configure_md_program():
    db = SessionLocal()
    try:
        # Find the MD Program 2026
        program = db.query(Program).filter(Program.code == "MD-2026").first()
        if not program:
            raise HTTPException(status_code=404, detail="MD Program 2026 not found")
        
        program_id = program.id
        
        # Create recommendation requirements
        # Two science faculty recommendations
        create_recommendation_requirement(
            program_id=program_id,
            req=CreateRecommendationRequirementRequest(
                requirement_type="Science Faculty",
                required_count=2
            ),
            db=db
        )
        
        # One clinical supervisor recommendation
        create_recommendation_requirement(
            program_id=program_id,
            req=CreateRecommendationRequirementRequest(
                requirement_type="Clinical Supervisor",
                required_count=1
            ),
            db=db
        )
        
        # Set recommendation submission deadline to December 15, 2025
        create_deadline_rule(
            program_id=program_id,
            req=CreateDeadlineRuleRequest(
                rule_name="MD Rec Deadline",
                deadline_type="Recommendation Submission",
                deadline_date=datetime(2025, 12, 15, 23, 59, 0),
                action_on_missing="Waitlist Review"
            ),
            db=db
        )
        
        # Set final application deadline to January 10, 2026
        create_deadline_rule(
            program_id=program_id,
            req=CreateDeadlineRuleRequest(
                rule_name="MD Final Deadline",
                deadline_type="Final Application",
                deadline_date=datetime(2026, 1, 10, 23, 59, 0),
                action_on_missing="Waitlist Review"
            ),
            db=db
        )
        
        # Enable automated reminder emails every 7 days starting December 1, 2025
        create_reminder_schedule(
            program_id=program_id,
            req=CreateReminderScheduleRequest(
                reminder_type="Recommendation Incomplete",
                interval_days=7,
                start_date=datetime(2025, 12, 1, 0, 0, 0),
                end_date=None
            ),
            db=db
        )
        
    except Exception as e:
        print(f"Error during configuration: {e}")
        raise
    finally:
        db.close()
