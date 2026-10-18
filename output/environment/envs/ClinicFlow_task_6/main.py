import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Body, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

# Global engine - initialized once
engine = None

class PatientSearchResult(BaseModel):
    id: int
    first_name: str
    last_name: str

class DoctorSearchResult(BaseModel):
    id: int
    full_name: str

class AppointmentDetail(BaseModel):
    id: int
    appointment_datetime: str
    duration_minutes: int

class CancelAppointmentRequest(BaseModel):
    cancelled_by: int

class CancelAppointmentResponse(BaseModel):
    message: str
    appointment_id: int

class AddToWaitlistRequest(BaseModel):
    patient_id: int
    doctor_id: int
    preferred_date: str
    preferred_time_start: str
    preferred_time_end: str
    reason: Optional[str] = None
    added_by: int

class AddToWaitlistResponse(BaseModel):
    message: str
    waitlist_id: int

def get_db():
    global engine
    if engine is None:
        db_path = os.environ.get("DATABASE_PATH")
        if not db_path:
            raise RuntimeError("DATABASE_PATH environment variable is not set")
        engine = create_engine(db_path)
    return engine

app = FastAPI()

@app.get("/patients/search", response_model=List[PatientSearchResult], operation_id="search_patients")
def search_patients(
    first_name: str = Query(..., description="First name of the patient"),
    last_name: str = Query(..., description="Last name of the patient")
):
    db = get_db()
    query = text("""
        SELECT id, first_name, last_name 
        FROM patients 
        WHERE lower(first_name) = lower(:first_name) AND lower(last_name) = lower(:last_name)
    """)
    with db.connect() as conn:
        result = conn.execute(query, {"first_name": first_name, "last_name": last_name})
        rows = result.fetchall()
    
    return [
        PatientSearchResult(id=row.id, first_name=row.first_name, last_name=row.last_name)
        for row in rows
    ]

@app.get("/doctors/search", response_model=List[DoctorSearchResult], operation_id="search_doctors")
def search_doctors(
    full_name: str = Query(..., description="Full name of the doctor")
):
    db = get_db()
    # Join doctors with users to get full_name
    query = text("""
        SELECT d.id, u.full_name 
        FROM doctors d
        JOIN users u ON d.user_id = u.id
        WHERE lower(u.full_name) LIKE lower(:full_name_pattern)
    """)
    pattern = f"%{full_name}%"
    with db.connect() as conn:
        result = conn.execute(query, {"full_name_pattern": pattern})
        rows = result.fetchall()
        
    return [
        DoctorSearchResult(id=row.id, full_name=row.full_name)
        for row in rows
    ]

@app.get("/appointments/scheduled", response_model=List[AppointmentDetail], operation_id="find_scheduled_appointments")
def find_scheduled_appointments(
    patient_id: int = Query(..., description="ID of the patient"),
    doctor_id: int = Query(..., description="ID of the doctor"),
    date: str = Query(..., description="Date of the appointment in YYYY-MM-DD format")
):
    db = get_db()
    # Filter by status 'scheduled' and match date part of appointment_datetime
    query = text("""
        SELECT id, appointment_datetime, duration_minutes 
        FROM appointments 
        WHERE patient_id = :patient_id 
          AND doctor_id = :doctor_id 
          AND status = 'scheduled'
          AND date(appointment_datetime) = :date
    """)
    with db.connect() as conn:
        result = conn.execute(query, {
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "date": date
        })
        rows = result.fetchall()
        
    return [
        AppointmentDetail(
            id=row.id,
            appointment_datetime=str(row.appointment_datetime),
            duration_minutes=row.duration_minutes
        )
        for row in rows
    ]

@app.put("/appointments/{appointment_id}/cancel", response_model=CancelAppointmentResponse, operation_id="cancel_appointment")
def cancel_appointment(
    appointment_id: int = Path(..., description="ID of the appointment to cancel"),
    request_body: CancelAppointmentRequest = Body(...)
):
    db = get_db()
    
    # Verify appointment exists and is scheduled
    check_query = text("""
        SELECT id, status FROM appointments WHERE id = :id
    """)
    with db.connect() as conn:
        res = conn.execute(check_query, {"id": appointment_id})
        appt = res.fetchone()
        
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    
    if appt.status != 'scheduled':
        raise HTTPException(status_code=400, detail=f"Cannot cancel appointment with status '{appt.status}'")

    # Perform cancellation
    update_query = text("""
        UPDATE appointments 
        SET status = 'cancelled', 
            cancelled_at = CURRENT_TIMESTAMP, 
            cancelled_by = :cancelled_by,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = :id
    """)
    
    with db.begin() as conn:
        conn.execute(update_query, {
            "id": appointment_id,
            "cancelled_by": request_body.cancelled_by
        })
        
    return CancelAppointmentResponse(
        message="Appointment cancelled successfully",
        appointment_id=appointment_id
    )

@app.post("/waitlist", response_model=AddToWaitlistResponse, operation_id="add_to_waitlist")
def add_to_waitlist(request_body: AddToWaitlistRequest):
    db = get_db()
    
    insert_query = text("""
        INSERT INTO waitlist (
            patient_id, 
            doctor_id, 
            preferred_date, 
            preferred_time_start, 
            preferred_time_end, 
            reason, 
            status, 
            priority, 
            created_at, 
            updated_at, 
            added_by
        ) VALUES (
            :patient_id, 
            :doctor_id, 
            :preferred_date, 
            :preferred_time_start, 
            :preferred_time_end, 
            :reason, 
            'waiting', 
            0, 
            CURRENT_TIMESTAMP, 
            CURRENT_TIMESTAMP, 
            :added_by
        )
    """)
    
    params = {
        "patient_id": request_body.patient_id,
        "doctor_id": request_body.doctor_id,
        "preferred_date": request_body.preferred_date,
        "preferred_time_start": request_body.preferred_time_start,
        "preferred_time_end": request_body.preferred_time_end,
        "reason": request_body.reason,
        "added_by": request_body.added_by
    }
    
    try:
        with db.begin() as conn:
            result = conn.execute(insert_query, params)
            new_id = result.lastrowid
            
        return AddToWaitlistResponse(
            message="Added to waitlist successfully",
            waitlist_id=new_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add to waitlist: {str(e)}")
