import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class AppointmentResponse(BaseModel):
    id: int
    doctor_name: str
    appointment_datetime: str
    status: str


class RescheduleRequest(BaseModel):
    new_datetime: str = Field(..., description="New datetime for the appointment")
    reason: str = Field(..., description="Reason for rescheduling")


class RescheduleResponse(BaseModel):
    id: int
    appointment_datetime: str
    status: str


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 string handling both 'T...Z' and space-separated formats."""
    # Replace Z with +00:00 for fromisoformat compatibility in older Python versions if needed,
    # but modern Python handles Z. However, we also need to handle space separator.
    dt_str = dt_str.strip()
    
    # Handle 'Z' suffix
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    # Try parsing directly first (handles T separator)
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        pass
        
    # If that fails, try replacing space with T
    if ' ' in dt_str:
        dt_str_t = dt_str.replace(' ', 'T', 1)
        try:
            return datetime.fromisoformat(dt_str_t)
        except ValueError:
            pass
            
    raise ValueError(f"Could not parse datetime string: {dt_str}")


@app.get("/appointments", response_model=List[AppointmentResponse], operation_id="find_my_upcoming_appointments")
def find_my_upcoming_appointments():
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        uid = int(current_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    query = text("""
        SELECT 
            a.id,
            d.full_name as doctor_name,
            a.appointment_datetime,
            a.status
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.user_id = :uid
          AND a.status = 'scheduled'
          AND a.appointment_datetime > :now
        ORDER BY a.appointment_datetime ASC
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"uid": uid, "now": now_utc})
        rows = result.fetchall()
        
        appointments = []
        for row in rows:
            appointments.append(AppointmentResponse(
                id=row.id,
                doctor_name=row.doctor_name,
                appointment_datetime=str(row.appointment_datetime),
                status=row.status
            ))
            
    return appointments


@app.put("/appointments/{appointment_id}", response_model=RescheduleResponse, operation_id="reschedule_appointment")
def reschedule_appointment(
    appointment_id: int = Path(..., description="Internal ID of the appointment to reschedule"),
    body: RescheduleRequest = Body(...)
):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        uid = int(current_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Parse new datetime
    try:
        new_dt_obj = parse_iso_datetime(body.new_datetime)
        # Normalize to UTC naive or aware depending on DB storage. 
        # The initial snapshot shows naive datetimes like "2024-05-10 10:00:00".
        # We will store it as a string compatible with SQLite DATETIME.
        # If input was Z/UTC, convert to naive UTC for consistency if DB stores naive UTC?
        # Looking at data: "2024-05-10 10:00:00". It doesn't specify TZ.
        # Let's assume the input represents local time or just needs to be stored consistently.
        # To be safe, let's strip tzinfo if present to match existing format, assuming inputs are aligned with server expectations.
        if new_dt_obj.tzinfo is not None:
             new_dt_str = new_dt_obj.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        else:
             new_dt_str = new_dt_obj.strftime("%Y-%m-%d %H:%M:%S")
             
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {str(e)}")

    with SessionLocal() as db:
        # Fetch appointment to verify ownership and get old datetime
        appt_query = text("""
            SELECT id, user_id, appointment_datetime, status
            FROM appointments
            WHERE id = :aid
        """)
        appt_row = db.execute(appt_query, {"aid": appointment_id}).fetchone()
        
        if not appt_row:
            raise HTTPException(status_code=404, detail="Appointment not found")
        
        if appt_row.user_id != uid:
            raise HTTPException(status_code=403, detail="Not authorized to modify this appointment")
            
        if appt_row.status != 'scheduled':
            raise HTTPException(status_code=400, detail="Only scheduled appointments can be rescheduled")
            
        old_datetime = appt_row.appointment_datetime
        
        # Update appointment
        update_appt_sql = text("""
            UPDATE appointments
            SET appointment_datetime = :new_dt,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :aid
        """)
        db.execute(update_appt_sql, {"new_dt": new_dt_str, "aid": appointment_id})
        
        # Insert history
        insert_hist_sql = text("""
            INSERT INTO appointment_history (appointment_id, old_datetime, new_datetime, changed_by, change_reason)
            VALUES (:aid, :old_dt, :new_dt, :cid, :reason)
        """)
        db.execute(insert_hist_sql, {
            "aid": appointment_id,
            "old_dt": old_datetime,
            "new_dt": new_dt_str,
            "cid": uid,
            "reason": body.reason
        })
        
        db.commit()
        
        # Return updated info
        return RescheduleResponse(
            id=appointment_id,
            appointment_datetime=new_dt_str,
            status="scheduled"
        )
