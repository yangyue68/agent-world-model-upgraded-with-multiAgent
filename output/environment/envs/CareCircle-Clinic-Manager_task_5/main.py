import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from contextlib import asynccontextmanager

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

class ProviderResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    specialty: Optional[str]
    email: str
    phone: str

class AppointmentTypeResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    duration_minutes: int

class FeeScheduleResponse(BaseModel):
    id: int
    provider_id: int
    appointment_type_id: int
    fee: float
    effective_date: str

class UpdateFeeRequest(BaseModel):
    fee: float

app = FastAPI()

@app.get("/providers/search", response_model=List[ProviderResponse], operation_id="search_providers")
def search_providers(
    first_name: Optional[str] = Query(None, description="The first name of the provider"),
    last_name: Optional[str] = Query(None, description="The last name of the provider")
):
    query = "SELECT id, first_name, last_name, specialty, email, phone FROM providers WHERE 1=1"
    params = {}
    
    if first_name:
        query += " AND first_name LIKE :first_name"
        params["first_name"] = f"%{first_name}%"
        
    if last_name:
        query += " AND last_name LIKE :last_name"
        params["last_name"] = f"%{last_name}%"
        
    with engine.connect() as conn:
        result = conn.execute(text(query), params).fetchall()
        
    return [
        {
            "id": row.id,
            "first_name": row.first_name,
            "last_name": row.last_name,
            "specialty": row.specialty,
            "email": row.email,
            "phone": row.phone
        }
        for row in result
    ]

@app.get("/appointment_types/search", response_model=List[AppointmentTypeResponse], operation_id="search_appointment_types")
def search_appointment_types(
    name: Optional[str] = Query(None, description="The name of the appointment type")
):
    query = "SELECT id, name, description, duration_minutes FROM appointment_types WHERE 1=1"
    params = {}
    
    if name:
        query += " AND name LIKE :name"
        params["name"] = f"%{name}%"
        
    with engine.connect() as conn:
        result = conn.execute(text(query), params).fetchall()
        
    return [
        {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "duration_minutes": row.duration_minutes
        }
        for row in result
    ]

@app.get("/fee_schedules/search", response_model=List[FeeScheduleResponse], operation_id="search_fee_schedules")
def search_fee_schedules(
    provider_id: Optional[int] = Query(None, description="The ID of the provider"),
    appointment_type_id: Optional[int] = Query(None, description="The ID of the appointment type")
):
    query = "SELECT id, provider_id, appointment_type_id, fee, effective_date FROM fee_schedules WHERE 1=1"
    params = {}
    
    if provider_id is not None:
        query += " AND provider_id = :provider_id"
        params["provider_id"] = provider_id
        
    if appointment_type_id is not None:
        query += " AND appointment_type_id = :appointment_type_id"
        params["appointment_type_id"] = appointment_type_id
        
    with engine.connect() as conn:
        result = conn.execute(text(query), params).fetchall()
        
    return [
        {
            "id": row.id,
            "provider_id": row.provider_id,
            "appointment_type_id": row.appointment_type_id,
            "fee": row.fee,
            "effective_date": str(row.effective_date)
        }
        for row in result
    ]

@app.put("/fee_schedules/{fee_schedule_id}", response_model=FeeScheduleResponse, operation_id="update_fee_schedule")
def update_fee_schedule(
    fee_schedule_id: int,
    body: UpdateFeeRequest
):
    # Read current user from environment variable injected by runtime
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: No user context found")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    # Verify existence and get details before update
    select_query = """
        SELECT id, provider_id, appointment_type_id, fee, effective_date 
        FROM fee_schedules 
        WHERE id = :id
    """
    
    with engine.begin() as conn:
        existing = conn.execute(text(select_query), {"id": fee_schedule_id}).fetchone()
        
        if not existing:
            raise HTTPException(status_code=404, detail="Fee schedule entry not found")
            
        # Perform update
        update_query = """
            UPDATE fee_schedules 
            SET fee = :new_fee, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """
        conn.execute(text(update_query), {"new_fee": body.fee, "id": fee_schedule_id})
        
        # Fetch updated record to return
        updated_record = conn.execute(text(select_query), {"id": fee_schedule_id}).fetchone()
        
    return {
        "id": updated_record.id,
        "provider_id": updated_record.provider_id,
        "appointment_type_id": updated_record.appointment_type_id,
        "fee": updated_record.fee,
        "effective_date": str(updated_record.effective_date)
    }
