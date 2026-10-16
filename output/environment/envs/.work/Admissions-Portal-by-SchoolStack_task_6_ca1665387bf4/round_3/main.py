import os
import sqlite3
from datetime import datetime, date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, CheckConstraint, ForeignKey, UniqueConstraint, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Database setup
DATABASE_PATH = "sqlite:///schoolstack.db"

# Remove 'sqlite:///' prefix to get file path
DB_FILE_PATH = DATABASE_PATH[10:] if DATABASE_PATH.startswith('sqlite:///') else DATABASE_PATH

# Create engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models (using raw SQL instead of ORM for simplicity)
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

# Helper functions
def get_program_id_by_name(db: Session, name: str) -> int:
    result = db.execute(
        "SELECT id FROM programs WHERE name LIKE ?",
        (f"%{name}%",)
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Program not found")
    return result[0]

def get_program_id_by_code(db: Session, code: str) -> int:
    result = db.execute(
        "SELECT id FROM programs WHERE code = ?",
        (code,)
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Program not found")
    return result[0]

# API Endpoints
app = FastAPI(title="SchoolStack Admissions Portal")

@app.get("/programs/lookup", response_model=List[dict], operation_id="lookup_programs")
def lookup_programs(
    name: Optional[str] = None,
    code: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Look up programs by name or code"""
    query = "SELECT id, name, code FROM programs WHERE 1=1"
    params = []
    
    if name:
        query += " AND name LIKE ?"
        params.append(f"%{name}%")
        
    if code:
        query += " AND code = ?"
        params.append(code)
        
    result = db.execute(query, params).fetchall()
    return [
        {
            "id": row[0],
            "name": row[1],
            "code": row[2]
        }
        for row in result
    ]

@app.post("/programs/{program_id}/recommendation-requirements", response_model=RecommendationRequirementResponse, operation_id="create_recommendation_requirement")
def create_recommendation_requirement(
    program_id: int,
    requirement: RecommendationRequirementCreate,
    db: Session = Depends(get_db)
):
    """Create a recommendation requirement for a program"""
    
    # Validate requirement type
    valid_types = ['Science Faculty', 'Clinical Supervisor', 'Other']
    if requirement.requirement_type not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid requirement type")
    
    # Check if requirement already exists for this program and type
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

@app.post("/programs/{program_id}/deadline-rules", response_model=DeadlineRuleResponse, operation_id="create_deadline_rule")
def create_deadline_rule(
    program_id: int,
    rule: DeadlineRuleCreate,
    db: Session = Depends(get_db)
):
    """Create a deadline rule for a program"""
    
    # Validate deadline type
    valid_types = ['Recommendation Submission', 'Final Application', 'Other']
    if rule.deadline_type not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid deadline type")
        
    # Validate action
    valid_actions = ['Waitlist Review', 'Reject', 'Notify']
    if rule.action_on_missing not in valid_actions:
        raise HTTPException(status_code=400, detail="Invalid action on missing")
    
    # Insert new rule
    db.execute(
        "INSERT INTO deadline_rules (program_id, rule_name, deadline_type, deadline_date, action_on_missing) VALUES (?, ?, ?, ?, ?)",
        (program_id, rule.rule_name, rule.deadline_type, rule.deadline_date, rule.action_on_missing)
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

@app.post("/programs/{program_id}/reminder-schedules", response_model=ReminderScheduleResponse, operation_id="create_reminder_schedule")
def create_reminder_schedule(
    program_id: int,
    reminder: ReminderScheduleCreate,
    db: Session = Depends(get_db)
):
    """Create an automated reminder schedule"""
    
    # Validate reminder type
    valid_types = ['Recommendation Incomplete', 'Application Incomplete', 'Other']
    if reminder.reminder_type not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid reminder type")
        
    # Insert new schedule
    db.execute(
        "INSERT INTO reminder_schedules (program_id, reminder_type, interval_days, start_date, end_date) VALUES (?, ?, ?, ?, ?)",
        (program_id, reminder.reminder_type, reminder.interval_days, reminder.start_date, reminder.end_date)
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

# Task-specific configuration
@app.on_event("startup")
def configure_md_program():
    """Configure MD Program 2026 with required settings"""
    db = SessionLocal()
    try:
        # Find the program ID for 'MD Program 2026'
        program_id = get_program_id_by_name(db, "MD Program 2026")
        
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
        
        # Create automated reminder schedule starting December 1, 2025
        create_reminder_schedule(
            program_id=program_id,
            reminder=ReminderScheduleCreate(
                reminder_type="Recommendation Incomplete",
                interval_days=7,
                start_date=datetime(2025, 12, 1, 0, 0, 0),
                end_date=datetime(2025, 12, 15, 23, 59, 0)
            ),
            db=db
        )
        
    except Exception as e:
        print(f"Error during configuration: {e}")
    finally:
        db.close()
