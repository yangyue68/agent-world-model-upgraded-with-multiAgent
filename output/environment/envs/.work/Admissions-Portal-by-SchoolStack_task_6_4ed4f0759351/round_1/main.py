import os
from datetime import datetime, date
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Path, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, text
from sqlalchemy.orm import declarative_base, Session

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH", "sqlite:///./schoolstack.db")
if DATABASE_PATH.startswith("sqlite:///"):
    db_file_path = DATABASE_PATH[len("sqlite:///"):]
else:
    # Fallback for other formats if needed, though spec says sqlite://
    db_file_path = None 

engine = create_engine(DATABASE_PATH, connect_args={"check_same_thread": False} if db_file_path else {})
Base = declarative_base()

# ORM Models matching the schema
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Program(Base):
    __tablename__ = 'programs'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    code = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Application(Base):
    __tablename__ = 'applications'
    id = Column(Integer, primary_key=True)
    program_id = Column(Integer, ForeignKey('programs.id', ondelete='CASCADE'), nullable=False)
    applicant_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    status = Column(String, nullable=False, default='Draft')
    submitted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class RecommendationRequirement(Base):
    __tablename__ = 'recommendation_requirements'
    id = Column(Integer, primary_key=True)
    program_id = Column(Integer, ForeignKey('programs.id', ondelete='CASCADE'), nullable=False)
    requirement_type = Column(String, nullable=False)
    required_count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('program_id', 'requirement_type', name='uq_program_req_type'),
        CheckConstraint("requirement_type IN ('Science Faculty','Clinical Supervisor','Other')", name='ck_rec_req_type'),
        CheckConstraint('required_count > 0', name='ck_rec_req_count'),
    )

class DeadlineRule(Base):
    __tablename__ = 'deadline_rules'
    id = Column(Integer, primary_key=True)
    program_id = Column(Integer, ForeignKey('programs.id', ondelete='CASCADE'), nullable=False)
    rule_name = Column(String, nullable=False)
    deadline_type = Column(String, nullable=False)
    deadline_date = Column(DateTime, nullable=False)
    action_on_missing = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("deadline_type IN ('Recommendation Submission','Final Application','Other')", name='ck_dl_type'),
        CheckConstraint("action_on_missing IN ('Waitlist Review','Reject','Notify')", name='ck_dl_action'),
    )

class ReminderSchedule(Base):
    __tablename__ = 'reminder_schedules'
    id = Column(Integer, primary_key=True)
    program_id = Column(Integer, ForeignKey('programs.id', ondelete='CASCADE'), nullable=False)
    reminder_type = Column(String, nullable=False)
    interval_days = Column(Integer, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("reminder_type IN ('Recommendation Incomplete','Application Incomplete','Other')", name='ck_rem_type'),
        CheckConstraint('interval_days > 0', name='ck_rem_interval'),
        CheckConstraint('is_active IN (0,1)', name='ck_rem_active'),
    )

# Pydantic Schemas
class ProgramLookupResponse(BaseModel):
    id: int
    name: str
    code: str

class RecommendationRequirementCreate(BaseModel):
    requirement_type: str = Field(..., description="Type of recommender: 'Science Faculty', 'Clinical Supervisor', or 'Other'")
    required_count: int = Field(..., gt=0, description="Number of recommendations of this type required")

class RecommendationRequirementResponse(BaseModel):
    id: int
    program_id: int
    requirement_type: str
    required_count: int

class DeadlineRuleCreate(BaseModel):
    rule_name: str = Field(..., description="Name of the rule")
    deadline_type: str = Field(..., description="Type of deadline: 'Recommendation Submission', 'Final Application', or 'Other'")
    deadline_date: datetime = Field(..., description="The date and time when the deadline occurs")
    action_on_missing: str = Field(..., description="Action to take if items are missing: 'Waitlist Review', 'Reject', or 'Notify'")

class DeadlineRuleResponse(BaseModel):
    id: int
    program_id: int
    rule_name: str
    deadline_type: str
    deadline_date: datetime
    action_on_missing: str

class ReminderScheduleCreate(BaseModel):
    reminder_type: str = Field(..., description="Type of reminder: 'Recommendation Incomplete', 'Application Incomplete', or 'Other'")
    interval_days: int = Field(..., gt=0, description="Number of days between reminders")
    start_date: datetime = Field(..., description="Date and time when reminders begin")
    end_date: Optional[datetime] = Field(None, description="Optional date and time when reminders stop")

class ReminderScheduleResponse(BaseModel):
    id: int
    program_id: int
    reminder_type: str
    interval_days: int
    start_date: datetime
    end_date: Optional[datetime]
    is_active: int

app = FastAPI(title="Admissions Portal API")

def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()

@app.get("/programs/lookup", response_model=List[ProgramLookupResponse], operation_id="lookup_programs")
def lookup_programs(
    name: Optional[str] = Query(None, description="Partial or exact match for program name"),
    code: Optional[str] = Query(None, description="Exact match for program code"),
    db: Session = Depends(get_db)
):
    query = db.query(Program)
    if name:
        query = query.filter(Program.name.ilike(f"%{name}%"))
    if code:
        query = query.filter(Program.code == code)
    
    programs = query.all()
    return [ProgramLookupResponse(id=p.id, name=p.name, code=p.code) for p in programs]

@app.post("/programs/{program_id}/recommendation-requirements", response_model=RecommendationRequirementResponse, operation_id="create_recommendation_requirement")
def create_recommendation_requirement(
    program_id: int = Path(..., description="ID of the program"),
    payload: RecommendationRequirementCreate = ...,
    db: Session = Depends(get_db)
):
    # Validate program exists
    program = db.query(Program).filter(Program.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Validate requirement type
    valid_types = ['Science Faculty', 'Clinical Supervisor', 'Other']
    if payload.requirement_type not in valid_types:
        raise HTTPException(status_code=422, detail=f"Invalid requirement_type. Must be one of {valid_types}")

    # Upsert logic based on UNIQUE constraint (program_id, requirement_type)
    existing_req = db.query(RecommendationRequirement).filter(
        RecommendationRequirement.program_id == program_id,
        RecommendationRequirement.requirement_type == payload.requirement_type
    ).first()

    if existing_req:
        existing_req.required_count = payload.required_count
        existing_req.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing_req)
        return RecommendationRequirementResponse(
            id=existing_req.id,
            program_id=existing_req.program_id,
            requirement_type=existing_req.requirement_type,
            required_count=existing_req.required_count
        )
    else:
        new_req = RecommendationRequirement(
            program_id=program_id,
            requirement_type=payload.requirement_type,
            required_count=payload.required_count
        )
        db.add(new_req)
        db.commit()
        db.refresh(new_req)
        return RecommendationRequirementResponse(
            id=new_req.id,
            program_id=new_req.program_id,
            requirement_type=new_req.requirement_type,
            required_count=new_req.required_count
        )

@app.post("/programs/{program_id}/deadline-rules", response_model=DeadlineRuleResponse, operation_id="create_deadline_rule")
def create_deadline_rule(
    program_id: int = Path(..., description="ID of the program"),
    payload: DeadlineRuleCreate = ...,
    db: Session = Depends(get_db)
):
    # Validate program exists
    program = db.query(Program).filter(Program.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Validate enums
    valid_deadline_types = ['Recommendation Submission', 'Final Application', 'Other']
    valid_actions = ['Waitlist Review', 'Reject', 'Notify']

    if payload.deadline_type not in valid_deadline_types:
        raise HTTPException(status_code=422, detail=f"Invalid deadline_type. Must be one of {valid_deadline_types}")
    
    if payload.action_on_missing not in valid_actions:
        raise HTTPException(status_code=422, detail=f"Invalid action_on_missing. Must be one of {valid_actions}")

    new_rule = DeadlineRule(
        program_id=program_id,
        rule_name=payload.rule_name,
        deadline_type=payload.deadline_type,
        deadline_date=payload.deadline_date,
        action_on_missing=payload.action_on_missing
    )
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)

    return DeadlineRuleResponse(
        id=new_rule.id,
        program_id=new_rule.program_id,
        rule_name=new_rule.rule_name,
        deadline_type=new_rule.deadline_type,
        deadline_date=new_rule.deadline_date,
        action_on_missing=new_rule.action_on_missing
    )

@app.post("/programs/{program_id}/reminder-schedules", response_model=ReminderScheduleResponse, operation_id="create_reminder_schedule")
def create_reminder_schedule(
    program_id: int = Path(..., description="ID of the program"),
    payload: ReminderScheduleCreate = ...,
    db: Session = Depends(get_db)
):
    # Validate program exists
    program = db.query(Program).filter(Program.id == program_id).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Validate enums
    valid_reminder_types = ['Recommendation Incomplete', 'Application Incomplete', 'Other']
    if payload.reminder_type not in valid_reminder_types:
        raise HTTPException(status_code=422, detail=f"Invalid reminder_type. Must be one of {valid_reminder_types}")

    if payload.interval_days <= 0:
        raise HTTPException(status_code=422, detail="interval_days must be greater than 0")

    new_schedule = ReminderSchedule(
        program_id=program_id,
        reminder_type=payload.reminder_type,
        interval_days=payload.interval_days,
        start_date=payload.start_date,
        end_date=payload.end_date,
        is_active=1
    )
    db.add(new_schedule)
    db.commit()
    db.refresh(new_schedule)

    return ReminderScheduleResponse(
        id=new_schedule.id,
        program_id=new_schedule.program_id,
        reminder_type=new_schedule.reminder_type,
        interval_days=new_schedule.interval_days,
        start_date=new_schedule.start_date,
        end_date=new_schedule.end_date,
        is_active=new_schedule.is_active
    )
