import os
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models for Responses ---

class PharmacyResponse(BaseModel):
    id: int
    name: str
    street_address: str
    city: str
    state: str


class ProviderResponse(BaseModel):
    id: int
    full_name: str


class AppointmentTypeResponse(BaseModel):
    id: int
    name: str
    duration_minutes: int


class InsurancePlanResponse(BaseModel):
    id: int
    insurance_id: str
    provider_name: str
    plan_name: str


class AppointmentBookingRequest(BaseModel):
    pharmacy_id: int
    provider_type: str
    appointment_type_id: int
    insurance_id: str
    appointment_datetime: str  # ISO format string


class AppointmentResponse(BaseModel):
    id: int
    status: str
    appointment_datetime: str


# --- Helper Functions ---

def get_current_user_id() -> int:
    """Retrieves the current user ID from the environment."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user ID format in environment")


def parse_iso_datetime(dt_string: str) -> datetime:
    """Parses ISO-8601 datetime strings, handling both 'T' and space separators."""
    dt_string = dt_string.strip()
    # Replace 'Z' with '+00:00' for compatibility if needed, though standard fromisoformat handles Z in newer Python versions
    # However, to be safe across versions and handle spaces:
    if dt_string.endswith('Z'):
        dt_string = dt_string[:-1] + '+00:00'
    
    # Try parsing with T separator first (standard ISO)
    try:
        return datetime.fromisoformat(dt_string)
    except ValueError:
        pass
    
    # Try replacing space with T if present (some formats use space)
    if ' ' in dt_string:
        try:
            normalized = dt_string.replace(' ', 'T', 1)
            return datetime.fromisoformat(normalized)
        except ValueError:
            pass
            
    raise HTTPException(status_code=400, detail=f"Invalid datetime format: {dt_string}")


# --- Endpoints ---

@app.get("/pharmacies", response_model=List[PharmacyResponse], operation_id="find_cvs_pharmacy")
def find_cvs_pharmacy(
    street_address: str = Query(..., description="Street address of the pharmacy"),
    city: str = Query(..., description="City of the pharmacy"),
    state: str = Query(..., description="State of the pharmacy"),
    has_minuteclinic: bool = Query(..., description="Whether the pharmacy has MinuteClinic service"),
    name: Optional[str] = Query(None, description="Name of the pharmacy (optional filter)")
):
    query = "SELECT id, name, street_address, city, state FROM pharmacies WHERE street_address = :street AND city = :city AND state = :state AND has_minuteclinic = :has_mc"
    params = {
        "street": street_address,
        "city": city,
        "state": state,
        "has_mc": 1 if has_minuteclinic else 0
    }
    
    if name:
        query += " AND name = :name"
        params["name"] = name
        
    with engine.connect() as conn:
        result = conn.execute(text(query), params).fetchall()
        
    return [
        PharmacyResponse(
            id=row.id,
            name=row.name,
            street_address=row.street_address,
            city=row.city,
            state=row.state
        ) for row in result
    ]


@app.get("/minuteclinic_providers", response_model=List[ProviderResponse], operation_id="find_nurse_practitioner")
def find_nurse_practitioner(
    pharmacy_id: int = Query(..., description="Internal ID of the pharmacy"),
    provider_type: str = Query(..., description="Type of provider (must be 'Nurse Practitioner')"),
    is_active: bool = Query(..., description="Whether the provider is currently active")
):
    query = "SELECT id, full_name FROM minuteclinic_providers WHERE pharmacy_id = :pid AND provider_type = :ptype AND is_active = :active"
    params = {
        "pid": pharmacy_id,
        "ptype": provider_type,
        "active": 1 if is_active else 0
    }
    
    with engine.connect() as conn:
        result = conn.execute(text(query), params).fetchall()
        
    return [
        ProviderResponse(
            id=row.id,
            full_name=row.full_name
        ) for row in result
    ]


@app.get("/appointment_types", response_model=List[AppointmentTypeResponse], operation_id="find_strep_throat_appointment_type")
def find_strep_throat_appointment_type(
    name: str = Query(..., description="Name of the appointment type")
):
    query = "SELECT id, name, duration_minutes FROM appointment_types WHERE name = :name"
    params = {"name": name}
    
    with engine.connect() as conn:
        result = conn.execute(text(query), params).fetchall()
        
    return [
        AppointmentTypeResponse(
            id=row.id,
            name=row.name,
            duration_minutes=row.duration_minutes
        ) for row in result
    ]


@app.get("/insurance_plans", response_model=List[InsurancePlanResponse], operation_id="validate_insurance")
def validate_insurance(
    insurance_id: str = Query(..., description="Insurance ID to validate"),
    is_active: bool = Query(..., description="Whether the insurance plan should be active")
):
    user_id = get_current_user_id()
    
    query = "SELECT id, insurance_id, provider_name, plan_name FROM insurance_plans WHERE user_id = :uid AND insurance_id = :iid AND is_active = :active"
    params = {
        "uid": user_id,
        "iid": insurance_id,
        "active": 1 if is_active else 0
    }
    
    with engine.connect() as conn:
        result = conn.execute(text(query), params).fetchall()
        
    return [
        InsurancePlanResponse(
            id=row.id,
            insurance_id=row.insurance_id,
            provider_name=row.provider_name,
            plan_name=row.plan_name
        ) for row in result
    ]


@app.post("/appointments", response_model=AppointmentResponse, operation_id="book_strep_throat_appointment")
def book_strep_throat_appointment(request: AppointmentBookingRequest):
    user_id = get_current_user_id()
    
    # 1. Validate Pharmacy exists and has MinuteClinic
    with engine.connect() as conn:
        pharmacy_check = conn.execute(
            text("SELECT id FROM pharmacies WHERE id = :pid AND has_minuteclinic = 1"),
            {"pid": request.pharmacy_id}
        ).fetchone()
        if not pharmacy_check:
            raise HTTPException(status_code=404, detail="Pharmacy not found or does not have MinuteClinic services.")

    # 2. Validate Appointment Type exists
    with engine.connect() as conn:
        appt_type_check = conn.execute(
            text("SELECT id FROM appointment_types WHERE id = :tid"),
            {"tid": request.appointment_type_id}
        ).fetchone()
        if not appt_type_check:
            raise HTTPException(status_code=404, detail="Appointment type not found.")

    # 3. Validate Insurance Plan belongs to user and is active
    with engine.connect() as conn:
        insurance_row = conn.execute(
            text("""
                SELECT id 
                FROM insurance_plans 
                WHERE user_id = :uid 
                  AND insurance_id = :iid 
                  AND is_active = 1
            """),
            {"uid": user_id, "iid": request.insurance_id}
        ).fetchone()
        
        if not insurance_row:
            raise HTTPException(status_code=400, detail="Invalid or inactive insurance ID for this user.")
        
        insurance_plan_db_id = insurance_row.id

    # 4. Find a suitable Nurse Practitioner at the pharmacy
    with engine.connect() as conn:
        providers = conn.execute(
            text("""
                SELECT id 
                FROM minuteclinic_providers 
                WHERE pharmacy_id = :pid 
                  AND provider_type = :ptype 
                  AND is_active = 1
                LIMIT 1
            """),
            {"pid": request.pharmacy_id, "ptype": request.provider_type}
        ).fetchall()
        
        if not providers:
            raise HTTPException(status_code=404, detail="No available nurse practitioners found at this location.")
        
        provider_id = providers[0].id

    # 5. Parse Datetime
    try:
        parsed_dt = parse_iso_datetime(request.appointment_datetime)
        # Store as naive datetime string compatible with SQLite DATETIME column usually expected as YYYY-MM-DD HH:MM:SS
        db_datetime_str = parsed_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 6. Check for slot conflict (Unique constraint on provider_id, appointment_datetime where status is scheduled/confirmed)
    # We rely on the DB unique index idx_appointments_provider_slot, but we can also check explicitly for better error messages
    with engine.connect() as conn:
        conflict = conn.execute(
            text("""
                SELECT id FROM appointments 
                WHERE provider_id = :prov_id 
                  AND appointment_datetime = :appt_dt 
                  AND status IN ('scheduled', 'confirmed')
            """),
            {"prov_id": provider_id, "appt_dt": db_datetime_str}
        ).fetchone()
        
        if conflict:
            raise HTTPException(status_code=409, detail="Time slot already booked for this provider.")

    # 7. Insert Appointment
    insert_query = text("""
        INSERT INTO appointments (
            user_id, 
            pharmacy_id, 
            provider_id, 
            appointment_type_id, 
            insurance_plan_id, 
            appointment_datetime, 
            status, 
            notes, 
            created_at, 
            updated_at
        ) VALUES (
            :uid, 
            :pid, 
            :prov_id, 
            :atid, 
            :ipid, 
            :adt, 
            'scheduled', 
            NULL, 
            CURRENT_TIMESTAMP, 
            CURRENT_TIMESTAMP
        ) RETURNING id, status, appointment_datetime
    """)
    
    params = {
        "uid": user_id,
        "pid": request.pharmacy_id,
        "prov_id": provider_id,
        "atid": request.appointment_type_id,
        "ipid": insurance_plan_db_id,
        "adt": db_datetime_str
    }
    
    with engine.begin() as conn:
        result = conn.execute(insert_query, params).fetchone()
        
    if not result:
        raise HTTPException(status_code=500, detail="Failed to book appointment.")
        
    return AppointmentResponse(
        id=result.id,
        status=result.status,
        appointment_datetime=result.appointment_datetime
    )
