import os
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class AppointmentResponse(BaseModel):
    id: int
    doctor_id: int
    doctor_name: str
    appointment_datetime: str
    status: str


class CancelAppointmentResponse(BaseModel):
    id: int
    status: str


class ScheduleFollowupRequest(BaseModel):
    doctor_id: int
    appointment_datetime: str


class ScheduleFollowupResponse(BaseModel):
    id: int
    doctor_id: int
    appointment_datetime: str
    status: str


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.get("/appointments", response_model=List[AppointmentResponse], operation_id="find_appointments")
def find_appointments(
    date: Optional[str] = Query(None, description="Filter appointments by date (YYYY-MM-DD format)"),
    time: Optional[str] = Query(None, description="Filter appointments by time (HH:MM format)")
):
    """
    Retrieve all appointments for the current user, filtered by date and time if specified.
    Returns appointment details including ID, doctor information, and status.
    """
    user_id = get_current_user_id()
    
    query = """
        SELECT 
            a.id,
            a.doctor_id,
            d.full_name as doctor_name,
            a.appointment_datetime,
            a.status
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.user_id = :user_id
    """
    params = {"user_id": user_id}

    if date:
        # Filter by date part of appointment_datetime
        query += " AND DATE(a.appointment_datetime) = :date"
        params["date"] = date
    
    if time:
        # Filter by time part of appointment_datetime
        query += " AND TIME(a.appointment_datetime) = :time"
        params["time"] = time

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        rows = result.fetchall()
        
        appointments = []
        for row in rows:
            appointments.append(AppointmentResponse(
                id=row.id,
                doctor_id=row.doctor_id,
                doctor_name=row.doctor_name,
                appointment_datetime=str(row.appointment_datetime),
                status=row.status
            ))
            
    return appointments


@app.put("/appointments/{id}/cancel", response_model=CancelAppointmentResponse, operation_id="cancel_appointment")
def cancel_appointment(id: int = Path(..., description="ID of the appointment to cancel")):
    """
    Update the status of an appointment to 'cancelled'. Only the owner of the appointment can cancel it.
    """
    user_id = get_current_user_id()
    
    with engine.begin() as conn:
        # First check if the appointment exists and belongs to the user
        check_query = text("""
            SELECT id, status FROM appointments WHERE id = :id AND user_id = :user_id
        """)
        result = conn.execute(check_query, {"id": id, "user_id": user_id})
        appointment = result.fetchone()
        
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found or does not belong to you")
        
        if appointment.status == 'cancelled':
             # Already cancelled, but we can still return success or handle accordingly. 
             # The spec says update status to cancelled. If already cancelled, no change needed logically, 
             # but let's proceed to ensure consistency or just return current state.
             pass
        
        # Update the status
        update_query = text("""
            UPDATE appointments 
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND user_id = :user_id
        """)
        conn.execute(update_query, {"id": id, "user_id": user_id})
        
    return CancelAppointmentResponse(id=id, status='cancelled')


@app.post("/appointments", response_model=ScheduleFollowupResponse, operation_id="schedule_followup")
def schedule_followup(request: ScheduleFollowupRequest = Body(...)):
    """
    Create a new appointment for the current user with the specified doctor at the given datetime.
    The appointment will be marked as 'scheduled'.
    """
    user_id = get_current_user_id()
    
    # Validate that the doctor exists
    with engine.connect() as conn:
        doc_check = text("SELECT id FROM doctors WHERE id = :doctor_id")
        result = conn.execute(doc_check, {"doctor_id": request.doctor_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Doctor not found")

    # Insert new appointment
    insert_query = text("""
        INSERT INTO appointments (user_id, doctor_id, appointment_datetime, status, created_at, updated_at)
        VALUES (:user_id, :doctor_id, :appointment_datetime, 'scheduled', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """)
    
    with engine.begin() as conn:
        result = conn.execute(insert_query, {
            "user_id": user_id,
            "doctor_id": request.doctor_id,
            "appointment_datetime": request.appointment_datetime
        })
        new_id = result.lastrowid
        
    return ScheduleFollowupResponse(
        id=new_id,
        doctor_id=request.doctor_id,
        appointment_datetime=request.appointment_datetime,
        status='scheduled'
    )
