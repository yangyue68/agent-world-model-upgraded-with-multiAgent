import os
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models for Responses ---

class TutorResponse(BaseModel):
    id: int
    display_name: str

class StudentResponse(BaseModel):
    id: int
    full_name: str

class SubjectResponse(BaseModel):
    id: int
    name: str

class PaymentMethodResponse(BaseModel):
    id: int
    card_brand: str
    last_four: str

class SessionCreateRequest(BaseModel):
    student_id: int
    tutor_id: int
    subject_id: Optional[int] = None
    payment_method_id: int
    start_time_utc: str
    notes: Optional[str] = None

class SessionResponse(BaseModel):
    id: int
    student_id: int
    tutor_id: int
    subject_id: Optional[int]
    booked_by_user_id: int
    payment_method_id: Optional[int]
    start_time_utc: str
    end_time_utc: str
    duration_minutes: int
    timezone: str
    status: str
    notes: Optional[str]


# --- Helper Functions ---

def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID format")

def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 string, handling 'Z' suffix and space separators."""
    # Replace 'Z' with '+00:00' for fromisoformat compatibility in older Python versions or just strip it if naive
    # The spec says start_time_utc is UTC. 
    # If the string ends with Z, it's UTC.
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    # Handle space separator instead of T
    if ' ' in dt_str and 'T' not in dt_str:
        dt_str = dt_str.replace(' ', 'T')
        
    try:
        dt = datetime.fromisoformat(dt_str)
        # Ensure it's treated as UTC if no tzinfo was provided (though we added +00:00 above if Z was present)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
        else:
            # Convert to UTC if it has a different timezone
            dt = dt.astimezone(dt_timezone.utc)
        return dt
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")

def format_datetime_for_db(dt: datetime) -> str:
    """Format datetime to SQLite DATETIME string (YYYY-MM-DD HH:MM:SS)."""
    # SQLite stores datetimes as strings. We assume UTC storage based on column name start_time_utc.
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# --- Endpoints ---

@app.get("/tutors", response_model=List[TutorResponse], operation_id="find_tutors")
def find_tutors(name: str = Query(..., description="Tutor display name")):
    query = text("""
        SELECT id, display_name 
        FROM tutors 
        WHERE display_name LIKE :name_pattern
    """)
    params = {"name_pattern": f"%{name}%"}
    
    with engine.connect() as conn:
        result = conn.execute(query, params)
        rows = result.fetchall()
        
    return [TutorResponse(id=row[0], display_name=row[1]) for row in rows]

@app.get("/students", response_model=List[StudentResponse], operation_id="find_students")
def find_students(name: str = Query(..., description="Student full name")):
    query = text("""
        SELECT id, full_name 
        FROM students 
        WHERE full_name LIKE :name_pattern
    """)
    params = {"name_pattern": f"%{name}%"}
    
    with engine.connect() as conn:
        result = conn.execute(query, params)
        rows = result.fetchall()
        
    return [StudentResponse(id=row[0], full_name=row[1]) for row in rows]

@app.get("/subjects", response_model=List[SubjectResponse], operation_id="find_subjects")
def find_subjects(name: str = Query(..., description="Subject name")):
    query = text("""
        SELECT id, name 
        FROM subjects 
        WHERE name LIKE :name_pattern
    """)
    params = {"name_pattern": f"%{name}%"}
    
    with engine.connect() as conn:
        result = conn.execute(query, params)
        rows = result.fetchall()
        
    return [SubjectResponse(id=row[0], name=row[1]) for row in rows]

@app.get("/payment_methods", response_model=List[PaymentMethodResponse], operation_id="find_payment_methods")
def find_payment_methods():
    current_user_id = get_current_user_id()
    
    query = text("""
        SELECT id, card_brand, last_four 
        FROM payment_methods 
        WHERE user_id = :user_id
    """)
    params = {"user_id": current_user_id}
    
    with engine.connect() as conn:
        result = conn.execute(query, params)
        rows = result.fetchall()
        
    return [PaymentMethodResponse(id=row[0], card_brand=row[1], last_four=row[2]) for row in rows]

@app.post("/sessions", response_model=SessionResponse, operation_id="create_session")
def create_session(session_data: SessionCreateRequest):
    current_user_id = get_current_user_id()
    
    # 1. Validate Tutor exists and is active
    tutor_query = text("SELECT id, timezone FROM tutors WHERE id = :id AND is_active = 1")
    with engine.connect() as conn:
        tutor_res = conn.execute(tutor_query, {"id": session_data.tutor_id}).fetchone()
        if not tutor_res:
            raise HTTPException(status_code=404, detail="Tutor not found or inactive")
        tutor_timezone = tutor_res[1]

    # 2. Validate Student exists
    student_query = text("SELECT id FROM students WHERE id = :id")
    with engine.connect() as conn:
        student_res = conn.execute(student_query, {"id": session_data.student_id}).fetchone()
        if not student_res:
            raise HTTPException(status_code=404, detail="Student not found")

    # 3. Validate Subject if provided
    if session_data.subject_id:
        subject_query = text("SELECT id FROM subjects WHERE id = :id")
        with engine.connect() as conn:
            subject_res = conn.execute(subject_query, {"id": session_data.subject_id}).fetchone()
            if not subject_res:
                raise HTTPException(status_code=404, detail="Subject not found")
            
            # Check if tutor teaches this subject? The schema doesn't strictly enforce this in DB constraints, 
            # but logically it might be required. However, the API spec doesn't say to validate this relationship.
            # I will skip strict validation of tutor-subject link unless specified, to avoid over-engineering.

    # 4. Validate Payment Method belongs to current user
    pm_query = text("SELECT id FROM payment_methods WHERE id = :id AND user_id = :user_id")
    with engine.connect() as conn:
        pm_res = conn.execute(pm_query, {"id": session_data.payment_method_id, "user_id": current_user_id}).fetchone()
        if not pm_res:
            raise HTTPException(status_code=403, detail="Payment method not found or does not belong to current user")

    # 5. Process Time
    # Input is start_time_utc. 
    # Duration is fixed at 60 minutes per task description ("Book a 60-minute...").
    # The API accepts start_time_utc. We calculate end_time_utc.
    
    start_dt_utc = parse_iso_datetime(session_data.start_time_utc)
    end_dt_utc = start_dt_utc + timedelta(minutes=60)
    
    duration_minutes = 60
    
    # Determine timezone for the session record. 
    # The schema has a `timezone` column in sessions. 
    # Usually, this reflects the local time context or the tutor/student preference.
    # Since the input is explicitly UTC, and the output requires a timezone field,
    # we should probably store the UTC offset or the specific timezone string.
    # Looking at existing data: "America/New_York", "America/Los_Angeles".
    # The task asks for EST. EST is America/New_York.
    # However, the API input is `start_time_utc`. 
    # If the user provides UTC time, what timezone do we save?
    # The `sessions` table has a `timezone` column. 
    # In the example data, `timezone` matches the location of the participants.
    # Let's use the Tutor's timezone as the default session timezone, or perhaps the User's timezone.
    # Given the ambiguity, using the Tutor's timezone (retrieved earlier) is a reasonable business logic choice 
    # for when the session takes place locally. Or we could use 'UTC'.
    # Let's look at the initial snapshot: Session 1 has timezone 'America/New_York'. Tutor 1 is 'America/New_York'.
    # Session 2 has timezone 'America/Los_Angeles'. Tutor 2 is 'America/Los_Angeles'.
    # It seems the session timezone matches the Tutor's timezone.
    session_timezone = tutor_timezone

    # 6. Insert Session
    insert_query = text("""
        INSERT INTO sessions (
            student_id, 
            tutor_id, 
            subject_id, 
            booked_by_user_id, 
            payment_method_id, 
            start_time_utc, 
            end_time_utc, 
            duration_minutes, 
            timezone, 
            status, 
            notes
        ) VALUES (
            :student_id, 
            :tutor_id, 
            :subject_id, 
            :booked_by_user_id, 
            :payment_method_id, 
            :start_time_utc, 
            :end_time_utc, 
            :duration_minutes, 
            :timezone, 
            :status, 
            :notes
        )
    """)
    
    params = {
        "student_id": session_data.student_id,
        "tutor_id": session_data.tutor_id,
        "subject_id": session_data.subject_id,
        "booked_by_user_id": current_user_id,
        "payment_method_id": session_data.payment_method_id,
        "start_time_utc": format_datetime_for_db(start_dt_utc),
        "end_time_utc": format_datetime_for_db(end_dt_utc),
        "duration_minutes": duration_minutes,
        "timezone": session_timezone,
        "status": "scheduled",
        "notes": session_data.notes
    }
    
    with engine.begin() as conn:
        result = conn.execute(insert_query, params)
        new_session_id = result.lastrowid
        
        # Fetch the created record to return it
        fetch_query = text("""
            SELECT id, student_id, tutor_id, subject_id, booked_by_user_id, payment_method_id, 
                   start_time_utc, end_time_utc, duration_minutes, timezone, status, notes 
            FROM sessions 
            WHERE id = :id
        """)
        row = conn.execute(fetch_query, {"id": new_session_id}).fetchone()
        
    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created session")
        
    return SessionResponse(
        id=row[0],
        student_id=row[1],
        tutor_id=row[2],
        subject_id=row[3],
        booked_by_user_id=row[4],
        payment_method_id=row[5],
        start_time_utc=row[6],
        end_time_utc=row[7],
        duration_minutes=row[8],
        timezone=row[9],
        status=row[10],
        notes=row[11]
    )
