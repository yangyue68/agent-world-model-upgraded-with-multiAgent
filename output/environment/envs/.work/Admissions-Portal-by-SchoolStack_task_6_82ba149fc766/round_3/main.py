import os
from datetime import datetime, date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, CheckConstraint, ForeignKey, func, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Database setup
DATABASE_PATH = "sqlite:///./test.db"

# Strip 'sqlite:///' prefix to get file path
DB_FILE_PATH = DATABASE_PATH[10:] if DATABASE_PATH.startswith("sqlite:///") else DATABASE_PATH

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models (matching schema exactly)
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

# Create tables
Base.metadata.create_all(bind=engine)

# Request/response models
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

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Helper functions
def get_program_by_name_or_code(db, name, code):
    query = db.query(Program)
    if name:
        query = query.filter(Program.name.contains(name))
    if code:
        query = query.filter(Program.code == code)
    return query.all()

def get_program_by_id(db, program_id):
    return db.query(Program).filter(Program.id == program_id).first()

def get_recommendation_requirement_by_type(db, program_id, requirement_type):
    return db.query(RecommendationRequirement).filter(
        and_(RecommendationRequirement.program_id == program_id,
             RecommendationRequirement.requirement_type == requirement_type)
    ).first()

def get_deadline_rule_by_type(db, program_id, deadline_type):
    return db.query(DeadlineRule).filter(
        and_(DeadlineRule.program_id == program_id,
             DeadlineRule.deadline_type == deadline_type)
    ).first()

def get_reminder_schedule_by_type(db, program_id, reminder_type):
    return db.query(ReminderSchedule).filter(
        and_(ReminderSchedule.program_id == program_id,
             ReminderSchedule.reminder_type == reminder_type)
    ).first()

# Endpoints
@app.get("/programs/lookup", response_model=List[dict])
def lookup_programs(
    name: Optional[str] = None,
    code: Optional[str] = None,
    db: Session = Depends(get_db)
):
    programs = get_program_by_name_or_code(db, name, code)
    return [
        {
            "id": p.id,
            "name": p.name,
            "code": p.code
        } for p in programs
    ]

@app.post("/programs/{program_id}/recommendation-requirements", response_model=dict)
def create_recommendation_requirement(
    program_id: int,
    req: CreateRecommendationRequirementRequest,
    db: Session = Depends(get_db)
):
    # Validate program exists
    program = get_program_by_id(db, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Check if requirement already exists
    existing = get_recommendation_requirement_by_type(db, program_id, req.requirement_type)
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
    # Validate program exists
    program = get_program_by_id(db, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Check if rule already exists for this type
    existing = get_deadline_rule_by_type(db, program_id, req.deadline_type)
    if existing:
        # Update existing
        existing.rule_name = req.rule_name
        existing.deadline_date = req.deadline_date
        existing.action_on_missing = req.action_on_missing
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return {
            "id": existing.id,
            "program_id": existing.program_id,
            "rule_name": existing.rule_name,
            "deadline_type": existing.deadline_type,
            "deadline_date": existing.deadline_date,
            "action_on_missing": existing.action_on_missing
        }
    else:
        # Create new
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
    # Validate program exists
    program = get_program_by_id(db, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Create new
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

# Configure the MD Program 2026 workflow
@app.on_event("startup")
def configure_md_program_workflow():
    db = SessionLocal()
    try:
        # Find the MD Program 2026
        md_program = db.query(Program).filter(Program.code == "MD-2026").first()
        if not md_program:
            raise HTTPException(status_code=404, detail="MD Program 2026 not found")

        # Create recommendation requirements
        # Two Science Faculty recommendations
        create_recommendation_requirement(md_program.id, 
                                          CreateRecommendationRequirementRequest(
                                              requirement_type="Science Faculty",
                                              required_count=2
                                          ), db)

        # One Clinical Supervisor recommendation
        create_recommendation_requirement(md_program.id, 
                                          CreateRecommendationRequirementRequest(
                                              requirement_type="Clinical Supervisor",
                                              required_count=1
                                          ), db)

        # Set recommendation submission deadline to December 15, 2025
        create_deadline_rule(md_program.id,
                             CreateDeadlineRuleRequest(
                                 rule_name="MD Rec Deadline",
                                 deadline_type="Recommendation Submission",
                                 deadline_date=datetime(2025, 12, 15, 23, 59, 0),
                                 action_on_missing="Waitlist Review"
                             ), db)

        # Set final application deadline to January 10, 2026
        create_deadline_rule(md_program.id,
                             CreateDeadlineRuleRequest(
                                 rule_name="MD Final Deadline",
                                 deadline_type="Final Application",
                                 deadline_date=datetime(2026, 1, 10, 23, 59, 0),
                                 action_on_missing="Waitlist Review"
                             ), db)

        # Enable automated reminder emails every 7 days starting December 1, 2025
        create_reminder_schedule(md_program.id,
                                 CreateReminderScheduleRequest(
                                     reminder_type="Recommendation Incomplete",
                                     interval_days=7,
                                     start_date=datetime(2025, 12, 1, 0, 0, 0),
                                     end_date=datetime(2025, 12, 15, 23, 59, 0)
                                 ), db)
    finally:
        db.close()
