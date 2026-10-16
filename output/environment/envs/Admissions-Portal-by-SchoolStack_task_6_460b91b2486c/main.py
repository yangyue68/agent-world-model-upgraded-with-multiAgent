import os
from datetime import datetime, date
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Read DATABASE_PATH at module import; never choose a fallback database.
DATABASE_PATH = os.environ["DATABASE_PATH"]

# Strip the exact 'sqlite:///' prefix to obtain its file path for sqlite3 usage if needed.
if DATABASE_PATH.startswith("sqlite:///"):
    DB_FILE_PATH = DATABASE_PATH[len("sqlite:///"):]
else:
    # For non-sqlite URLs, we still use SQLAlchemy engine directly.
    DB_FILE_PATH = None

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Admissions Portal by SchoolStack", version="1.0.0")


# --- Pydantic Models ---

class ProgramLookupResponse(BaseModel):
    id: int
    name: str
    code: str


class RecommendationRequirementCreateRequest(BaseModel):
    requirement_type: str = Field(..., description="Type of recommender: 'Science Faculty', 'Clinical Supervisor', or 'Other'")
    required_count: int = Field(..., description="Number of recommendations of this type required")


class RecommendationRequirementResponse(BaseModel):
    id: int
    program_id: int
    requirement_type: str
    required_count: int


class DeadlineRuleCreateRequest(BaseModel):
    rule_name: str = Field(..., description="Name of the rule (e.g., 'MD Rec Deadline')")
    deadline_type: str = Field(..., description="Type of deadline: 'Recommendation Submission', 'Final Application', or 'Other'")
    deadline_date: str = Field(..., description="The date and time when the deadline occurs")
    action_on_missing: str = Field(..., description="Action to take if items are missing: 'Waitlist Review', 'Reject', or 'Notify'")


class DeadlineRuleResponse(BaseModel):
    id: int
    program_id: int
    rule_name: str
    deadline_type: str
    deadline_date: str
    action_on_missing: str


class ReminderScheduleCreateRequest(BaseModel):
    reminder_type: str = Field(..., description="Type of reminder: 'Recommendation Incomplete', 'Application Incomplete', or 'Other'")
    interval_days: int = Field(..., description="Number of days between reminders")
    start_date: str = Field(..., description="Date and time when reminders begin")
    end_date: Optional[str] = Field(None, description="Optional date and time when reminders stop")


class ReminderScheduleResponse(BaseModel):
    id: int
    program_id: int
    reminder_type: str
    interval_days: int
    start_date: str
    end_date: Optional[str] = None
    is_active: int


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO format datetime string."""
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {dt_str}")


def validate_requirement_type(req_type: str):
    valid_types = ['Science Faculty', 'Clinical Supervisor', 'Other']
    if req_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid requirement_type. Must be one of: {valid_types}")


def validate_deadline_type(dl_type: str):
    valid_types = ['Recommendation Submission', 'Final Application', 'Other']
    if dl_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid deadline_type. Must be one of: {valid_types}")


def validate_action(action: str):
    valid_actions = ['Waitlist Review', 'Reject', 'Notify']
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"Invalid action_on_missing. Must be one of: {valid_actions}")


def validate_reminder_type(rt: str):
    valid_types = ['Recommendation Incomplete', 'Application Incomplete', 'Other']
    if rt not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid reminder_type. Must be one of: {valid_types}")


# --- Endpoints ---

@app.get("/programs/lookup", response_model=List[ProgramLookupResponse], operation_id="lookup_programs", tags=["ProgramConfiguration"])
def lookup_programs(
    name: Optional[str] = Query(None, description="Partial or exact match for program name"),
    code: Optional[str] = Query(None, description="Exact match for program code")
):
    """Look up programs by name or code."""
    db = next(get_db())
    try:
        query = "SELECT id, name, code FROM programs WHERE 1=1"
        params = {}
        
        if name is not None:
            query += " AND name LIKE :name_pattern"
            params["name_pattern"] = f"%{name}%"
        
        if code is not None:
            query += " AND code = :code"
            params["code"] = code
        
        result = db.execute(text(query), params).fetchall()
        
        return [
            ProgramLookupResponse(id=row.id, name=row.name, code=row.code)
            for row in result
        ]
    finally:
        db.close()


@app.post("/programs/{program_id}/recommendation-requirements", response_model=RecommendationRequirementResponse, operation_id="create_recommendation_requirement", tags=["ProgramConfiguration"])
def create_recommendation_requirement(
    program_id: int = Path(..., description="ID of the program"),
    body: RecommendationRequirementCreateRequest = ...
):
    """Create a recommendation requirement for a program."""
    validate_requirement_type(body.requirement_type)
    
    db = next(get_db())
    try:
        # Check if program exists
        prog_result = db.execute(text("SELECT id FROM programs WHERE id = :pid"), {"pid": program_id}).fetchone()
        if not prog_result:
            raise HTTPException(status_code=404, detail=f"Program with ID {program_id} not found")
        
        # Check if requirement already exists for this program and type
        existing = db.execute(
            text("SELECT id, program_id, requirement_type, required_count FROM recommendation_requirements WHERE program_id = :pid AND requirement_type = :rtype"),
            {"pid": program_id, "rtype": body.requirement_type}
        ).fetchone()
        
        if existing:
            # Update existing requirement
            db.execute(
                text("UPDATE recommendation_requirements SET required_count = :count, updated_at = CURRENT_TIMESTAMP WHERE id = :rid"),
                {"count": body.required_count, "rid": existing.id}
            )
            db.commit()
            # Fetch updated record
            updated = db.execute(
                text("SELECT id, program_id, requirement_type, required_count FROM recommendation_requirements WHERE id = :rid"),
                {"rid": existing.id}
            ).fetchone()
            return RecommendationRequirementResponse(
                id=updated.id,
                program_id=updated.program_id,
                requirement_type=updated.requirement_type,
                required_count=updated.required_count
            )
        else:
            # Insert new requirement
            result = db.execute(
                text("""INSERT INTO recommendation_requirements (program_id, requirement_type, required_count, created_at, updated_at) 
                        VALUES (:pid, :rtype, :count, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""),
                {"pid": program_id, "rtype": body.requirement_type, "count": body.required_count}
            )
            db.commit()
            new_id = result.lastrowid
            new_record = db.execute(
                text("SELECT id, program_id, requirement_type, required_count FROM recommendation_requirements WHERE id = :rid"),
                {"rid": new_id}
            ).fetchone()
            return RecommendationRequirementResponse(
                id=new_record.id,
                program_id=new_record.program_id,
                requirement_type=new_record.requirement_type,
                required_count=new_record.required_count
            )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/programs/{program_id}/deadline-rules", response_model=DeadlineRuleResponse, operation_id="create_deadline_rule", tags=["ProgramConfiguration"])
def create_deadline_rule(
    program_id: int = Path(..., description="ID of the program"),
    body: DeadlineRuleCreateRequest = ...
):
    """Create a deadline rule for a program."""
    validate_deadline_type(body.deadline_type)
    validate_action(body.action_on_missing)
    
    parsed_date = parse_datetime(body.deadline_date)
    
    db = next(get_db())
    try:
        # Check if program exists
        prog_result = db.execute(text("SELECT id FROM programs WHERE id = :pid"), {"pid": program_id}).fetchone()
        if not prog_result:
            raise HTTPException(status_code=404, detail=f"Program with ID {program_id} not found")
        
        result = db.execute(
            text("""INSERT INTO deadline_rules (program_id, rule_name, deadline_type, deadline_date, action_on_missing, created_at, updated_at) 
                    VALUES (:pid, :rname, :dtype, :ddate, :action, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""),
            {
                "pid": program_id,
                "rname": body.rule_name,
                "dtype": body.deadline_type,
                "ddate": parsed_date.strftime("%Y-%m-%d %H:%M:%S"),
                "action": body.action_on_missing
            }
        )
        db.commit()
        new_id = result.lastrowid
        new_record = db.execute(
            text("SELECT id, program_id, rule_name, deadline_type, deadline_date, action_on_missing FROM deadline_rules WHERE id = :rid"),
            {"rid": new_id}
        ).fetchone()
        
        return DeadlineRuleResponse(
            id=new_record.id,
            program_id=new_record.program_id,
            rule_name=new_record.rule_name,
            deadline_type=new_record.deadline_type,
            deadline_date=new_record.deadline_date,
            action_on_missing=new_record.action_on_missing
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/programs/{program_id}/reminder-schedules", response_model=ReminderScheduleResponse, operation_id="create_reminder_schedule", tags=["ProgramConfiguration"])
def create_reminder_schedule(
    program_id: int = Path(..., description="ID of the program"),
    body: ReminderScheduleCreateRequest = ...
):
    """Create an automated reminder schedule."""
    validate_reminder_type(body.reminder_type)
    
    if body.interval_days <= 0:
        raise HTTPException(status_code=400, detail="interval_days must be greater than 0")
    
    parsed_start = parse_datetime(body.start_date)
    parsed_end = None
    if body.end_date:
        parsed_end = parse_datetime(body.end_date)
    
    db = next(get_db())
    try:
        # Check if program exists
        prog_result = db.execute(text("SELECT id FROM programs WHERE id = :pid"), {"pid": program_id}).fetchone()
        if not prog_result:
            raise HTTPException(status_code=404, detail=f"Program with ID {program_id} not found")
        
        insert_params = {
            "pid": program_id,
            "rtype": body.reminder_type,
            "interval": body.interval_days,
            "start": parsed_start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": parsed_end.strftime("%Y-%m-%d %H:%M:%S") if parsed_end else None,
            "active": 1
        }
        
        if parsed_end:
            result = db.execute(
                text("""INSERT INTO reminder_schedules (program_id, reminder_type, interval_days, start_date, end_date, is_active, created_at, updated_at) 
                        VALUES (:pid, :rtype, :interval, :start, :end, :active, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""),
                insert_params
            )
        else:
            result = db.execute(
                text("""INSERT INTO reminder_schedules (program_id, reminder_type, interval_days, start_date, end_date, is_active, created_at, updated_at) 
                        VALUES (:pid, :rtype, :interval, :start, NULL, :active, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""),
                insert_params
            )
        
        db.commit()
        new_id = result.lastrowid
        new_record = db.execute(
            text("SELECT id, program_id, reminder_type, interval_days, start_date, end_date, is_active FROM reminder_schedules WHERE id = :rid"),
            {"rid": new_id}
        ).fetchone()
        
        return ReminderScheduleResponse(
            id=new_record.id,
            program_id=new_record.program_id,
            reminder_type=new_record.reminder_type,
            interval_days=new_record.interval_days,
            start_date=new_record.start_date,
            end_date=new_record.end_date,
            is_active=new_record.is_active
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
