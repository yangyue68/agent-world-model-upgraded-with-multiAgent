import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ProviderResponse(BaseModel):
    id: int
    user_id: int
    specialty_id: int
    license_number: str
    bio: Optional[str] = None


class AppointmentTypeResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None


class ScheduleAppointmentRequest(BaseModel):
    patient_id: int
    provider_id: int
    appointment_type_id: int
    scheduled_start: str
    scheduled_end: str


class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    provider_id: int
    appointment_type_id: int
    scheduled_start: str
    scheduled_end: str
    status: str
    video_link: Optional[str] = None
    notes: Optional[str] = None


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string supporting both T...Z and space-separated formats."""
    dt_str = dt_str.strip()
    # Handle Z suffix
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        pass
    
    # Try common formats
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    
    raise ValueError(f"Unable to parse datetime: {dt_str}")


@app.get("/providers/specialty/{specialty_name}", response_model=List[ProviderResponse], operation_id="find_providers_by_specialty")
def find_providers_by_specialty(specialty_name: str):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT p.id, p.user_id, p.specialty_id, p.license_number, p.bio
                FROM providers p
                JOIN specialties s ON p.specialty_id = s.id
                WHERE LOWER(s.name) = LOWER(:specialty_name)
            """),
            {"specialty_name": specialty_name}
        )
        rows = result.fetchall()
        
        providers = []
        for row in rows:
            providers.append(ProviderResponse(
                id=row[0],
                user_id=row[1],
                specialty_id=row[2],
                license_number=row[3],
                bio=row[4]
            ))
        
        return providers


@app.get("/appointment_types", response_model=List[AppointmentTypeResponse], operation_id="list_appointment_types")
def list_appointment_types():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, name, description FROM appointment_types"))
        rows = result.fetchall()
        
        types = []
        for row in rows:
            types.append(AppointmentTypeResponse(
                id=row[0],
                name=row[1],
                description=row[2]
            ))
        
        return types


@app.post("/appointments", response_model=AppointmentResponse, operation_id="schedule_appointment")
def schedule_appointment(request: ScheduleAppointmentRequest):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    current_user_id_int = int(current_user_id)
    
    # Verify the patient_id matches the current user (scheduling for myself)
    if request.patient_id != current_user_id_int:
        raise HTTPException(status_code=403, detail="Can only schedule appointments for yourself")
    
    # Parse datetimes
    try:
        start_dt = parse_datetime(request.scheduled_start)
        end_dt = parse_datetime(request.scheduled_end)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Validate that start < end
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="Scheduled end must be after scheduled start")
    
    with engine.begin() as conn:
        # Verify provider exists
        provider_check = conn.execute(
            text("SELECT id FROM providers WHERE id = :provider_id"),
            {"provider_id": request.provider_id}
        ).fetchone()
        if not provider_check:
            raise HTTPException(status_code=404, detail="Provider not found")
        
        # Verify appointment type exists
        appt_type_check = conn.execute(
            text("SELECT id FROM appointment_types WHERE id = :appt_type_id"),
            {"appt_type_id": request.appointment_type_id}
        ).fetchone()
        if not appt_type_check:
            raise HTTPException(status_code=404, detail="Appointment type not found")
        
        # Insert the appointment
        formatted_start = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        formatted_end = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        insert_result = conn.execute(
            text("""
                INSERT INTO appointments (patient_id, provider_id, appointment_type_id, scheduled_start, scheduled_end, status)
                VALUES (:patient_id, :provider_id, :appointment_type_id, :scheduled_start, :scheduled_end, 'scheduled')
            """),
            {
                "patient_id": request.patient_id,
                "provider_id": request.provider_id,
                "appointment_type_id": request.appointment_type_id,
                "scheduled_start": formatted_start,
                "scheduled_end": formatted_end
            }
        )
        
        new_id = insert_result.lastrowid
        
        # Fetch the created appointment
        result = conn.execute(
            text("""
                SELECT id, patient_id, provider_id, appointment_type_id, scheduled_start, scheduled_end, status, video_link, notes
                FROM appointments WHERE id = :id
            """),
            {"id": new_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=500, detail="Failed to retrieve created appointment")
        
        return AppointmentResponse(
            id=result[0],
            patient_id=result[1],
            provider_id=result[2],
            appointment_type_id=result[3],
            scheduled_start=result[4],
            scheduled_end=result[5],
            status=result[6],
            video_link=result[7],
            notes=result[8]
        )
