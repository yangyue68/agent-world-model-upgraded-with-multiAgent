import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CareCircle Clinic Manager API")


# --- Pydantic Models ---

class ProviderResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    specialty: Optional[str] = None


class CreateAppointmentTypeRequest(BaseModel):
    name: str
    duration_minutes: int
    default_fee: float


class AppointmentTypeResponse(BaseModel):
    id: int
    name: str
    duration_minutes: int
    default_fee: float


class AssignProviderAppointmentTypeRequest(BaseModel):
    provider_id: int
    appointment_type_id: int


class ProviderAppointmentTypeResponse(BaseModel):
    id: int
    provider_id: int
    appointment_type_id: int


# --- Endpoints ---

@app.get("/providers", response_model=List[ProviderResponse], operation_id="find_providers")
def find_providers(
    first_name: Optional[str] = Query(None, description="First name of the provider"),
    last_name: Optional[str] = Query(None, description="Last name of the provider"),
):
    """
    Search for providers by their first and last name. 
    Returns a list of matching providers with their IDs, names, and specialties.
    """
    query = "SELECT id, first_name, last_name, specialty FROM providers WHERE 1=1"
    params = {}

    if first_name:
        query += " AND first_name LIKE :first_name"
        params["first_name"] = f"%{first_name}%"
    
    if last_name:
        query += " AND last_name LIKE :last_name"
        params["last_name"] = f"%{last_name}%"

    try:
        with engine.connect() as conn:
            result = conn.execute(text(query), params)
            rows = result.fetchall()
            
            providers = []
            for row in rows:
                providers.append({
                    "id": row.id,
                    "first_name": row.first_name,
                    "last_name": row.last_name,
                    "specialty": row.specialty
                })
            return providers
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.post("/appointment_types", response_model=AppointmentTypeResponse, operation_id="create_appointment_type")
def create_appointment_type(request: CreateAppointmentTypeRequest):
    """
    Creates a new appointment type with the specified name, duration, and default fee.
    """
    # Check if name already exists to provide better error message, though DB constraint will catch it too
    check_query = text("SELECT id FROM appointment_types WHERE name = :name")
    
    insert_query = text("""
        INSERT INTO appointment_types (name, duration_minutes, default_fee)
        VALUES (:name, :duration_minutes, :default_fee)
    """)
    
    select_query = text("""
        SELECT id, name, duration_minutes, default_fee 
        FROM appointment_types 
        WHERE id = :id
    """)

    try:
        with engine.begin() as conn:
            # Insert new record
            res = conn.execute(insert_query, {
                "name": request.name,
                "duration_minutes": request.duration_minutes,
                "default_fee": request.default_fee
            })
            new_id = res.lastrowid
            
            # Fetch back to ensure consistency and get all fields
            result = conn.execute(select_query, {"id": new_id})
            row = result.fetchone()
            
            if not row:
                raise HTTPException(status_code=500, detail="Failed to retrieve created appointment type")
                
            return {
                "id": row.id,
                "name": row.name,
                "duration_minutes": row.duration_minutes,
                "default_fee": row.default_fee
            }
    except Exception as e:
        # Handle unique constraint violation specifically if needed, otherwise generic 400/500
        if "UNIQUE constraint failed" in str(e):
             raise HTTPException(status_code=400, detail="Appointment type with this name already exists")
        raise HTTPException(status_code=500, detail=f"Error creating appointment type: {str(e)}")


@app.post("/provider_appointment_types", response_model=ProviderAppointmentTypeResponse, operation_id="assign_appointment_type_to_provider")
def assign_appointment_type_to_provider(request: AssignProviderAppointmentTypeRequest):
    """
    Assigns a specific appointment type to a provider.
    """
    insert_query = text("""
        INSERT INTO provider_appointment_types (provider_id, appointment_type_id)
        VALUES (:provider_id, :appointment_type_id)
    """)
    
    select_query = text("""
        SELECT id, provider_id, appointment_type_id 
        FROM provider_appointment_types 
        WHERE id = :id
    """)

    try:
        with engine.begin() as conn:
            # Verify provider exists
            prov_check = conn.execute(text("SELECT id FROM providers WHERE id = :pid"), {"pid": request.provider_id}).fetchone()
            if not prov_check:
                raise HTTPException(status_code=404, detail="Provider not found")
            
            # Verify appointment type exists
            at_check = conn.execute(text("SELECT id FROM appointment_types WHERE id = :atid"), {"atid": request.appointment_type_id}).fetchone()
            if not at_check:
                raise HTTPException(status_code=404, detail="Appointment type not found")

            # Insert assignment
            res = conn.execute(insert_query, {
                "provider_id": request.provider_id,
                "appointment_type_id": request.appointment_type_id
            })
            new_id = res.lastrowid
            
            # Fetch back
            result = conn.execute(select_query, {"id": new_id})
            row = result.fetchone()
            
            if not row:
                 raise HTTPException(status_code=500, detail="Failed to retrieve created assignment")

            return {
                "id": row.id,
                "provider_id": row.provider_id,
                "appointment_type_id": row.appointment_type_id
            }
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="This appointment type is already assigned to this provider")
        raise HTTPException(status_code=500, detail=f"Error assigning appointment type: {str(e)}")
