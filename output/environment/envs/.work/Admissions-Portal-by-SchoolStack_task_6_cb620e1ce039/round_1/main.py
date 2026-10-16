import os
import sqlite3
from datetime import datetime, date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable must be set")

# Strip the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH.replace('sqlite:///', '')

# Create engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models (using raw SQL for simplicity as per instructions)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Request and Response Models
class ProgramLookupRequest(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None

class RecommendationRequirementCreate(BaseModel):
    requirement_type: str  # 'Science Faculty', 'Clinical Supervisor', or 'Other'
    required_count: int

class RecommendationRequirementResponse(BaseModel):
    id: int
    program_id: int
    requirement_type: str
    required_count: int

class DeadlineRuleCreate(BaseModel):
    rule_name: str
    deadline_type: str  # 'Recommendation Submission', 'Final Application', or 'Other'
    deadline_date: datetime
    action_on_missing: str  # 'Waitlist Review', 'Reject', or 'Notify'

class DeadlineRuleResponse(BaseModel):
    id: int
    program_id: int
    rule_name: str
    deadline_type: str
    deadline_date: datetime
    action_on_missing: str

class ReminderScheduleCreate(BaseModel):
    reminder_type: str  # 'Recommendation Incomplete', 'Application Incomplete', or 'Other'
    interval_days: int
    start_date: datetime
    end_date: Optional[datetime] = None

class ReminderScheduleResponse(BaseModel):
    id: int
    program_id: int
    reminder_type: str
    interval_days: int
    start_date: datetime
    end_date: Optional[datetime] = None
    is_active: int

# FastAPI app
app = FastAPI()

# Helper functions
def get_program_id(db: Session, program_name: str = None, program_code: str = None) -> int:
    query = "SELECT id FROM programs WHERE 1=1"
    params = []
    
    if program_name:
        query += " AND name LIKE ?"
        params.append(f"%{program_name}%")
        
    if program_code:
        query += " AND code = ?"
        params.append(program_code)
        
    result = db.execute(query, params).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Program not found")
    return result[0]

def check_program_exists(db: Session, program_id: int) -> bool:
    result = db.execute("SELECT 1 FROM programs WHERE id = ?", (program_id,)).fetchone()
    return result is not None

# Endpoints
@app.get("/programs/lookup", response_model=List[dict])
def lookup_programs(
    name: Optional[str] = None,
    code: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = "SELECT id, name, code FROM programs WHERE 1=1"
    params = []
    
    if name:
        query += " AND name LIKE ?"
        params.append(f"%{name}%")
        
    if code:
        query += " AND code = ?"
        params.append(code)
        
    results = db.execute(query, params).fetchall()
    return [
        {"id": row[0], "name": row[1], "code": row[2]}
        for row in results
    ]

@app.post("/programs/{program_id}/recommendation-requirements", response_model=RecommendationRequirementResponse)
def create_recommendation_requirement(
    program_id: int,
    requirement: RecommendationRequirementCreate,
    db: Session = Depends(get_db)
):
    if not check_program_exists(db, program_id):
        raise HTTPException(status_code=404, detail="Program not found")
        
    # Check if requirement already exists for this type
    existing = db.execute(
        "SELECT id FROM recommendation_requirements WHERE program_id = ? AND requirement_type = ?",
        (program_id, requirement.requirement_type)
    ).fetchone()
    
    if existing:
        # Update existing
        db.execute(
            "UPDATE recommendation_requirements SET required_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (requirement.required_count, existing[0])
        )
        db.commit()
        result = db.execute(
            "SELECT * FROM recommendation_requirements WHERE id = ?",
            (existing[0],)
        ).fetchone()
    else:
        # Insert new
        db.execute(
            "INSERT INTO recommendation_requirements (program_id, requirement_type, required_count) VALUES (?, ?, ?)",
            (program_id, requirement.requirement_type, requirement.required_count)
        )
        db.commit()
        result = db.execute(
            "SELECT * FROM recommendation_requirements WHERE program_id = ? AND requirement_type = ?",
            (program_id, requirement.requirement_type)
        ).fetchone()
    
    return {
        "id": result[0],
        "program_id": result[1],
        "requirement_type": result[2],
        "required_count": result[3]
    }

@app.post("/programs/{program_id}/deadline-rules", response_model=DeadlineRuleResponse)
def create_deadline_rule(
    program_id: int,
    rule: DeadlineRuleCreate,
    db: Session = Depends(get_db)
):
    if not check_program_exists(db, program_id):
        raise HTTPException(status_code=404, detail="Program not found")
        
    db.execute(
        "INSERT INTO deadline_rules (program_id, rule_name, deadline_type, deadline_date, action_on_missing) VALUES (?, ?, ?, ?, ?)",
        (
            program_id,
            rule.rule_name,
            rule.deadline_type,
            rule.deadline_date,
            rule.action_on_missing
        )
    )
    db.commit()
    
    result = db.execute(
        "SELECT * FROM deadline_rules WHERE program_id = ? AND rule_name = ?",
        (program_id, rule.rule_name)
    ).fetchone()
    
    return {
        "id": result[0],
        "program_id": result[1],
        "rule_name": result[2],
        "deadline_type": result[3],
        "deadline_date": result[4],
        "action_on_missing": result[5]
    }

@app.post("/programs/{program_id}/reminder-schedules", response_model=ReminderScheduleResponse)
def create_reminder_schedule(
    program_id: int,
    reminder: ReminderScheduleCreate,
    db: Session = Depends(get_db)
):
    if not check_program_exists(db, program_id):
        raise HTTPException(status_code=404, detail="Program not found")
        
    db.execute(
        "INSERT INTO reminder_schedules (program_id, reminder_type, interval_days, start_date, end_date) VALUES (?, ?, ?, ?, ?)",
        (
            program_id,
            reminder.reminder_type,
            reminder.interval_days,
            reminder.start_date,
            reminder.end_date
        )
    )
    db.commit()
    
    result = db.execute(
        "SELECT * FROM reminder_schedules WHERE program_id = ? AND reminder_type = ?",
        (program_id, reminder.reminder_type)
    ).fetchone()
    
    return {
        "id": result[0],
        "program_id": result[1],
        "reminder_type": result[2],
        "interval_days": result[3],
        "start_date": result[4],
        "end_date": result[5],
        "is_active": result[6]
    }

# Configure MD Program 2026 workflow
@app.post("/configure-md-program-workflow", response_model=dict)
def configure_md_program_workflow_endpoint(db: Session = Depends(get_db)):
    # Find the program ID for 'MD Program 2026'
    program_id = get_program_id(db, program_name="MD Program 2026")
    
    # Create recommendation requirements
    # Two Science Faculty recommendations
    create_recommendation_requirement(
        program_id=program_id,
        requirement=RecommendationRequirementCreate(
            requirement_type="Science Faculty",
            required_count=2
        ),
        db=db
    )
    
    # One Clinical Supervisor recommendation
    create_recommendation_requirement(
        program_id=program_id,
        requirement=RecommendationRequirementCreate(
            requirement_type="Clinical Supervisor",
            required_count=1
        ),
        db=db
    )
    
    # Set recommendation submission deadline to December 15, 2025
    create_deadline_rule(
        program_id=program_id,
        rule=DeadlineRuleCreate(
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
        rule=DeadlineRuleCreate(
            rule_name="MD Final Application Deadline",
            deadline_type="Final Application",
            deadline_date=datetime(2026, 1, 10, 23, 59, 0),
            action_on_missing="Waitlist Review"
        ),
        db=db
    )
    
    # Enable automated reminder emails every 7 days starting December 1, 2025
    create_reminder_schedule(
        program_id=program_id,
        reminder=ReminderScheduleCreate(
            reminder_type="Recommendation Incomplete",
            interval_days=7,
            start_date=datetime(2025, 12, 1, 0, 0, 0),
            end_date=None
        ),
        db=db
    )
    
    return {
        "message": "MD Program 2026 workflow configured successfully",
        "program_id": program_id
    }
