import os
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RescheduleRequest(BaseModel):
    scheduled_start: str
    scheduled_end: str


class PatientSearchResponse(BaseModel):
    id: int
    full_name: str
    phone: str
    email: str
    sms_opt_in: int


class ProviderSearchResponse(BaseModel):
    id: int
    full_name: str
    phone: str
    email: str


class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    provider_id: int
    scheduled_start: str
    scheduled_end: str
    status: str
    notification_created: bool = False


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both T...Z and space-separated forms."""
    if dt_str is None:
        raise ValueError("Datetime string cannot be None")
    
    # Replace 'T' with space for uniform parsing if present
    cleaned = dt_str.replace('T', ' ')
    
    # Remove trailing 'Z' if present (UTC indicator)
    if cleaned.endswith('Z'):
        cleaned = cleaned[:-1]
    
    # Try parsing with seconds
    try:
        return datetime.strptime(cleaned.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    
    # Try parsing without seconds
    try:
        return datetime.strptime(cleaned.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        pass
    
    raise ValueError(f"Unable to parse datetime: {dt_str}")


@app.get("/patients/search", response_model=list[PatientSearchResponse], operation_id="find_patient_by_name")
def find_patient_by_name(full_name: str = Query(..., description="Full name of the patient to search for")):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, full_name, phone, email, sms_opt_in FROM patients WHERE full_name LIKE :name"),
            {"name": f"%{full_name}%"}
        )
        rows = result.fetchall()
        
        results = []
        for row in rows:
            results.append(PatientSearchResponse(
                id=row.id,
                full_name=row.full_name,
                phone=row.phone or "",
                email=row.email or "",
                sms_opt_in=row.sms_opt_in
            ))
        return results


@app.get("/providers/search", response_model=list[ProviderSearchResponse], operation_id="find_provider_by_name")
def find_provider_by_name(full_name: str = Query(..., description="Full name of the provider to search for")):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, full_name, phone, email FROM providers WHERE full_name LIKE :name"),
            {"name": f"%{full_name}%"}
        )
        rows = result.fetchall()
        
        results = []
        for row in rows:
            results.append(ProviderSearchResponse(
                id=row.id,
                full_name=row.full_name,
                phone=row.phone or "",
                email=row.email or ""
            ))
        return results


@app.put("/appointments/{appointment_id}", response_model=AppointmentResponse, operation_id="reschedule_appointment")
def reschedule_appointment(appointment_id: int, body: RescheduleRequest):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Missing user context")
    
    try:
        new_start = parse_datetime(body.scheduled_start)
        new_end = parse_datetime(body.scheduled_end)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    
    if new_end <= new_start:
        raise HTTPException(status_code=422, detail="scheduled_end must be after scheduled_start")
    
    with engine.begin() as conn:
        # Fetch appointment details
        appt_result = conn.execute(
            text("""
                SELECT a.id, a.patient_id, a.provider_id, a.scheduled_start, a.scheduled_end, a.status,
                       p.full_name as patient_name, p.phone as patient_phone, p.email as patient_email, p.sms_opt_in,
                       pr.full_name as provider_name
                FROM appointments a
                JOIN patients p ON a.patient_id = p.id
                JOIN providers pr ON a.provider_id = pr.id
                WHERE a.id = :appt_id
            """),
            {"appt_id": appointment_id}
        )
        appt_row = appt_result.fetchone()
        
        if not appt_row:
            raise HTTPException(status_code=404, detail="Appointment not found")
        
        if appt_row.status != "scheduled":
            raise HTTPException(status_code=409, detail="Appointment must be in 'scheduled' status to reschedule")
        
        old_status = appt_row.status
        
        # Update appointment
        formatted_start = new_start.strftime("%Y-%m-%d %H:%M:%S")
        formatted_end = new_end.strftime("%Y-%m-%d %H:%M:%S")
        
        conn.execute(
            text("""
                UPDATE appointments 
                SET scheduled_start = :start, scheduled_end = :end, updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """),
            {"start": formatted_start, "end": formatted_end, "id": appointment_id}
        )
        
        # Log status history (no change in status, but we can log the reschedule action)
        # The spec says it creates a notification. It doesn't explicitly say it changes status.
        # However, usually rescheduling might keep status as 'scheduled'. 
        # Let's check if we need to add to history. The prompt says "This operation also creates a notification".
        # It does not mention changing status. So status remains 'scheduled'.
        # We don't necessarily need to add to appointment_status_history unless status changed.
        # But let's stick to what's required: update times, create notification.
        
        notification_created = False
        
        # Create SMS notification if patient opted in
        if appt_row.sms_opt_in == 1 and appt_row.patient_phone:
            # Format message
            start_dt = new_start
            end_dt = new_end
            
            # Human readable format for message
            month_names = ["January", "February", "March", "April", "May", "June", 
                           "July", "August", "September", "October", "November", "December"]
            
            day = start_dt.day
            month = month_names[start_dt.month - 1]
            year = start_dt.year
            
            hour = start_dt.hour
            minute = start_dt.minute
            am_pm = "AM" if hour < 12 else "PM"
            display_hour = hour if hour <= 12 else hour - 12
            if display_hour == 0:
                display_hour = 12
            
            time_str = f"{display_hour}:{minute:02d} {am_pm}"
            
            message = f"Reminder: Your appointment is rescheduled for {month} {day}, {year} at {time_str} with {appt_row.provider_name}."
            
            conn.execute(
                text("""
                    INSERT INTO notifications (patient_id, appointment_id, channel, recipient, message, status, created_by, created_at, updated_at)
                    VALUES (:patient_id, :appointment_id, 'sms', :recipient, :message, 'pending', :created_by, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {
                    "patient_id": appt_row.patient_id,
                    "appointment_id": appointment_id,
                    "recipient": appt_row.patient_phone,
                    "message": message,
                    "created_by": int(current_user_id)
                }
            )
            notification_created = True
        
        # Return updated appointment info
        return AppointmentResponse(
            id=appt_row.id,
            patient_id=appt_row.patient_id,
            provider_id=appt_row.provider_id,
            scheduled_start=formatted_start,
            scheduled_end=formatted_end,
            status=old_status,
            notification_created=notification_created
        )
