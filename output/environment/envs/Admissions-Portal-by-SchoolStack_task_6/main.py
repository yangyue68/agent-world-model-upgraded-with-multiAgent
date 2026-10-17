import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Admissions Portal API")


# --- Pydantic Models ---

class ProgramLookupResponse(BaseModel):
    id: int
    name: str
    code: str

class RecommendationRequirementCreate(BaseModel):
    requirement_type: str = Field(..., description="Type of recommender: 'Science Faculty', 'Clinical Supervisor', or 'Other'")
    required_count: int = Field(..., description="Number of recommendations of this type required")

class RecommendationRequirementResponse(BaseModel):
    id: int
    program_id: int
    requirement_type: str
    required_count: int

class DeadlineRuleCreate(BaseModel):
    rule_name: str = Field(..., description="Name of the rule")
    deadline_type: str = Field(..., description="Type of deadline: 'Recommendation Submission', 'Final Application', or 'Other'")
    deadline_date: str = Field(..., description="The date and time when the deadline occurs (ISO-8601)")
    action_on_missing: str = Field(..., description="Action to take if items are missing: 'Waitlist Review', 'Reject', or 'Notify'")

class DeadlineRuleResponse(BaseModel):
    id: int
    program_id: int
    rule_name: str
    deadline_type: str
    deadline_date: str
    action_on_missing: str

class ReminderScheduleCreate(BaseModel):
    reminder_type: str = Field(..., description="Type of reminder: 'Recommendation Incomplete', 'Application Incomplete', or 'Other'")
    interval_days: int = Field(..., description="Number of days between reminders")
    start_date: str = Field(..., description="Date and time when reminders begin (ISO-8601)")
    end_date: Optional[str] = Field(None, description="Optional date and time when reminders stop (ISO-8601)")

class ReminderScheduleResponse(BaseModel):
    id: int
    program_id: int
    reminder_type: str
    interval_days: int
    start_date: str
    end_date: Optional[str]
    is_active: int


# --- Helper Functions ---

def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 string handling both T...Z and space-separated forms."""
    if not dt_str:
        return None
    
    # Normalize format: replace 'T' with ' ' and remove 'Z' for simpler parsing if needed, 
    # but standard fromisoformat in Python 3.7+ handles most cases. 
    # However, SQLite DATETIME usually expects YYYY-MM-DD HH:MM:SS.
    
    try:
        # Try direct ISO parsing first
        if 'T' in dt_str:
            # Handle Z suffix
            if dt_str.endswith('Z'):
                dt_str = dt_str[:-1] + '+00:00'
            dt = datetime.fromisoformat(dt_str)
        else:
            # Assume space separated
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        
        # If naive, assume UTC? Or just keep as is for DB storage which is often naive local/UTC mix.
        # The schema uses DATETIME. We will store as string formatted for SQLite.
        return dt
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date-time format: {dt_str}")

def format_datetime_for_db(dt: datetime) -> str:
    if dt is None:
        return None
    # Return string compatible with SQLite DATETIME
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def get_program_by_id(db, program_id: int):
    result = db.execute(text("SELECT id, name, code FROM programs WHERE id = :id"), {"id": program_id}).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Program not found")
    return result

# --- Endpoints ---

@app.get("/programs/lookup", response_model=List[ProgramLookupResponse], operation_id="lookup_programs")
def lookup_programs(
    name: Optional[str] = Query(None, description="Partial or exact match for program name"),
    code: Optional[str] = Query(None, description="Exact match for program code")
):
    db = SessionLocal()
    try:
        query = "SELECT id, name, code FROM programs WHERE 1=1"
        params = {}
        
        if name:
            query += " AND name LIKE :name"
            params["name"] = f"%{name}%"
            
        if code:
            query += " AND code = :code"
            params["code"] = code
            
        results = db.execute(text(query), params).fetchall()
        
        return [
            ProgramLookupResponse(id=r.id, name=r.name, code=r.code)
            for r in results
        ]
    finally:
        db.close()


@app.post("/programs/{program_id}/recommendation-requirements", response_model=RecommendationRequirementResponse, operation_id="create_recommendation_requirement")
def create_recommendation_requirement(
    program_id: int,
    body: RecommendationRequirementCreate
):
    db = SessionLocal()
    try:
        # Validate program exists
        get_program_by_id(db, program_id)
        
        # Check valid enum values for requirement_type
        valid_types = ['Science Faculty', 'Clinical Supervisor', 'Other']
        if body.requirement_type not in valid_types:
            raise HTTPException(status_code=400, detail=f"Invalid requirement_type. Must be one of {valid_types}")
            
        if body.required_count <= 0:
            raise HTTPException(status_code=400, detail="required_count must be greater than 0")

        # Upsert logic: Update existing if unique constraint (program_id, requirement_type) matches, else insert
        existing = db.execute(
            text("SELECT id FROM recommendation_requirements WHERE program_id = :pid AND requirement_type = :rt"),
            {"pid": program_id, "rt": body.requirement_type}
        ).fetchone()
        
        now = datetime.now(timezone.utc)
        now_str = format_datetime_for_db(now)
        
        if existing:
            req_id = existing.id
            db.execute(
                text("""
                    UPDATE recommendation_requirements 
                    SET required_count = :count, updated_at = :updated_at
                    WHERE id = :id
                """),
                {"count": body.required_count, "updated_at": now_str, "id": req_id}
            )
        else:
            result = db.execute(
                text("""
                    INSERT INTO recommendation_requirements 
                    (program_id, requirement_type, required_count, created_at, updated_at)
                    VALUES (:pid, :rt, :count, :created_at, :updated_at)
                """),
                {
                    "pid": program_id, 
                    "rt": body.requirement_type, 
                    "count": body.required_count,
                    "created_at": now_str,
                    "updated_at": now_str
                }
            )
            req_id = result.lastrowid
            
        db.commit()
        
        return RecommendationRequirementResponse(
            id=req_id,
            program_id=program_id,
            requirement_type=body.requirement_type,
            required_count=body.required_count
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/programs/{program_id}/deadline-rules", response_model=DeadlineRuleResponse, operation_id="create_deadline_rule")
def create_deadline_rule(
    program_id: int,
    body: DeadlineRuleCreate
):
    db = SessionLocal()
    try:
        # Validate program exists
        get_program_by_id(db, program_id)
        
        # Validate enums
        valid_deadline_types = ['Recommendation Submission', 'Final Application', 'Other']
        valid_actions = ['Waitlist Review', 'Reject', 'Notify']
        
        if body.deadline_type not in valid_deadline_types:
            raise HTTPException(status_code=400, detail=f"Invalid deadline_type. Must be one of {valid_deadline_types}")
            
        if body.action_on_missing not in valid_actions:
            raise HTTPException(status_code=400, detail=f"Invalid action_on_missing. Must be one of {valid_actions}")
            
        # Parse dates
        deadline_dt = parse_datetime(body.deadline_date)
        deadline_str = format_datetime_for_db(deadline_dt)
        
        now = datetime.now(timezone.utc)
        now_str = format_datetime_for_db(now)
        
        result = db.execute(
            text("""
                INSERT INTO deadline_rules 
                (program_id, rule_name, deadline_type, deadline_date, action_on_missing, created_at, updated_at)
                VALUES (:pid, :rn, :dt, :dd, :am, :ca, :ua)
            """),
            {
                "pid": program_id,
                "rn": body.rule_name,
                "dt": body.deadline_type,
                "dd": deadline_str,
                "am": body.action_on_missing,
                "ca": now_str,
                "ua": now_str
            }
        )
        db.commit()
        
        rule_id = result.lastrowid
        
        return DeadlineRuleResponse(
            id=rule_id,
            program_id=program_id,
            rule_name=body.rule_name,
            deadline_type=body.deadline_type,
            deadline_date=body.deadline_date, # Return original input format or normalized? Spec says format date-time. Usually return what was stored or normalized. Let's return normalized string.
            action_on_missing=body.action_on_missing
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/programs/{program_id}/reminder-schedules", response_model=ReminderScheduleResponse, operation_id="create_reminder_schedule")
def create_reminder_schedule(
    program_id: int,
    body: ReminderScheduleCreate
):
    db = SessionLocal()
    try:
        # Validate program exists
        get_program_by_id(db, program_id)
        
        # Validate enums
        valid_reminder_types = ['Recommendation Incomplete', 'Application Incomplete', 'Other']
        if body.reminder_type not in valid_reminder_types:
            raise HTTPException(status_code=400, detail=f"Invalid reminder_type. Must be one of {valid_reminder_types}")
            
        if body.interval_days <= 0:
            raise HTTPException(status_code=400, detail="interval_days must be greater than 0")
            
        # Parse dates
        start_dt = parse_datetime(body.start_date)
        start_str = format_datetime_for_db(start_dt)
        
        end_str = None
        if body.end_date:
            end_dt = parse_datetime(body.end_date)
            end_str = format_datetime_for_db(end_dt)
            
        now = datetime.now(timezone.utc)
        now_str = format_datetime_for_db(now)
        
        result = db.execute(
            text("""
                INSERT INTO reminder_schedules 
                (program_id, reminder_type, interval_days, start_date, end_date, is_active, created_at, updated_at)
                VALUES (:pid, :rt, :id, :sd, :ed, :ia, :ca, :ua)
            """),
            {
                "pid": program_id,
                "rt": body.reminder_type,
                "id": body.interval_days,
                "sd": start_str,
                "ed": end_str,
                "ia": 1, # Default active
                "ca": now_str,
                "ua": now_str
            }
        )
        db.commit()
        
        schedule_id = result.lastrowid
        
        return ReminderScheduleResponse(
            id=schedule_id,
            program_id=program_id,
            reminder_type=body.reminder_type,
            interval_days=body.interval_days,
            start_date=body.start_date,
            end_date=body.end_date,
            is_active=1
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
