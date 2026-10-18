from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
import os
import uuid
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH") # Placeholder, actual value injected by runtime
engine = create_engine(DATABASE_PATH)

app = FastAPI()

class AppointmentCreate(BaseModel):
    patient_id: int
    doctor_id: int
    scheduled_at: str
    duration_minutes: int = 30
    reason: Optional[str] = None
    recurrence_rule: Optional[str] = None

class PatientSearchResult(BaseModel):
    id: int
    full_name: str

class DoctorSearchResult(BaseModel):
    id: int
    full_name: str

def parse_rrule_count(rule: str) -> int:
    """Extract COUNT from RRULE string like 'FREQ=WEEKLY;COUNT=4'"""
    if not rule:
        return 1
    parts = rule.split(';')
    for part in parts:
        if part.startswith('COUNT='):
            try:
                return int(part.split('=')[1])
            except ValueError:
                pass
    return 1

@app.post("/appointments", operation_id="create_appointment")
def create_appointment(payload: AppointmentCreate):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    created_by = int(current_user_id_str) if current_user_id_str else None
    
    # Parse scheduled_at to ensure valid format and use for calculations
    try:
        # Handle both ISO with T/Z and space-separated
        dt_str = payload.scheduled_at.replace('Z', '+00:00')
        if 'T' in dt_str:
             base_dt = datetime.fromisoformat(dt_str)
        else:
             base_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date format")

    count = parse_rrule_count(payload.recurrence_rule)
    
    appointments_to_insert = []
    recurrence_group_id = None
    
    if count > 1:
        recurrence_group_id = str(uuid.uuid4())
        
    for i in range(count):
        if i == 0:
            sched_dt = base_dt
        else:
            # Assuming WEEKLY frequency based on task description context, 
            # but strictly we should parse FREQ. For simplicity and given the task says "recurring weekly",
            # we assume weekly if multiple occurrences are requested via COUNT.
            # If FREQ is not parsed, defaulting to weekly increment as per task scenario.
            freq_part = ""
            if payload.recurrence_rule:
                for p in payload.recurrence_rule.split(';'):
                    if p.startswith('FREQ='):
                        freq_part = p.split('=')[1]
            
            if freq_part == "WEEKLY":
                sched_dt = base_dt + timedelta(weeks=i)
            elif freq_part == "DAILY":
                sched_dt = base_dt + timedelta(days=i)
            else:
                # Default to weekly if unspecified or unknown, matching task intent
                sched_dt = base_dt + timedelta(weeks=i)
                
        appointments_to_insert.append({
            "patient_id": payload.patient_id,
            "doctor_id": payload.doctor_id,
            "scheduled_at": sched_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_minutes": payload.duration_minutes,
            "reason": payload.reason,
            "status": "scheduled",
            "recurrence_group_id": recurrence_group_id,
            "recurrence_rule": payload.recurrence_rule,
            "created_by": created_by,
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        })

    with engine.connect() as conn:
        inserted_ids = []
        for appt in appointments_to_insert:
            result = conn.execute(text("""
                INSERT INTO appointments (patient_id, doctor_id, scheduled_at, duration_minutes, reason, status, recurrence_group_id, recurrence_rule, created_by, created_at, updated_at)
                VALUES (:patient_id, :doctor_id, :scheduled_at, :duration_minutes, :reason, :status, :recurrence_group_id, :recurrence_rule, :created_by, :created_at, :updated_at)
            """), appt)
            inserted_ids.append(result.lastrowid)
        conn.commit()

    # Return the first appointment as the primary response object, 
    # though typically a list might be expected for recurring, the spec returns a single object structure.
    # The spec says "response": {"type": "object"...}. It implies returning one representative or the main one.
    # Given the schema, I will return the details of the first created appointment.
    
    first_appt = appointments_to_insert[0]
    first_appt["id"] = inserted_ids[0]
    
    return {
        "id": first_appt["id"],
        "patient_id": first_appt["patient_id"],
        "doctor_id": first_appt["doctor_id"],
        "scheduled_at": first_appt["scheduled_at"],
        "duration_minutes": first_appt["duration_minutes"],
        "reason": first_appt["reason"],
        "status": first_appt["status"],
        "recurrence_group_id": first_appt["recurrence_group_id"],
        "recurrence_rule": first_appt["recurrence_rule"],
        "created_by": first_appt["created_by"],
        "created_at": first_appt["created_at"],
        "updated_at": first_appt["updated_at"]
    }

@app.get("/patients/search", operation_id="search_patients", response_model=List[PatientSearchResult])
def search_patients(name: str = Query(...)):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, full_name FROM patients WHERE full_name LIKE :name"), {"name": f"%{name}%"})
        rows = result.fetchall()
        return [{"id": r[0], "full_name": r[1]} for r in rows]

@app.get("/doctors/search", operation_id="search_doctors", response_model=List[DoctorSearchResult])
def search_doctors(name: str = Query(...)):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, full_name FROM doctors WHERE full_name LIKE :name"), {"name": f"%{name}%"})
        rows = result.fetchall()
        return [{"id": r[0], "full_name": r[1]} for r in rows]
