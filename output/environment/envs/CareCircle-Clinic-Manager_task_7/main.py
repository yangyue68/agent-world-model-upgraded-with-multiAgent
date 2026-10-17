import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CareCircle Clinic Manager API")


class SetDefaultRequest(BaseModel):
    appointment_type_id: int


class SuccessResponse(BaseModel):
    success: bool
    message: str


class AppointmentTypeResponse(BaseModel):
    id: int
    name: str
    description: str


class ProviderAppointmentTypeResponse(BaseModel):
    id: int
    name: str
    description: str
    is_enabled: bool


@app.put("/appointment-types/default", response_model=SuccessResponse, operation_id="set_default_appointment_type")
def set_default_appointment_type(request_body: SetDefaultRequest):
    """
    Update the appointment type with the given ID to be the default for new patients.
    This will disable any previously set default appointment type.
    """
    db = SessionLocal()
    try:
        # Check if the requested appointment type exists
        result = db.execute(text("SELECT id FROM appointment_types WHERE id = :id"), {"id": request_body.appointment_type_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Appointment type not found")

        # Disable all current defaults
        db.execute(text("UPDATE appointment_types SET is_default_for_new_patients = 0 WHERE is_default_for_new_patients = 1"))
        
        # Enable the selected one as default
        db.execute(text("UPDATE appointment_types SET is_default_for_new_patients = 1 WHERE id = :id"), {"id": request_body.appointment_type_id})
        
        db.commit()
        return SuccessResponse(success=True, message=f"Default appointment type set to {request_body.appointment_type_id}")
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/providers/{provider_id}/appointment-types/{appointment_type_id}/enable", response_model=SuccessResponse, operation_id="enable_appointment_type_for_provider")
def enable_appointment_type_for_provider(
    provider_id: int = Path(..., description="The ID of the provider"),
    appointment_type_id: int = Path(..., description="The ID of the appointment type to enable")
):
    """
    Enable the specified appointment type for the given provider.
    If the provider does not already have this appointment type configured, it will be added with is_enabled = 1.
    """
    db = SessionLocal()
    try:
        # Verify provider exists
        prov_res = db.execute(text("SELECT id FROM providers WHERE id = :pid"), {"pid": provider_id})
        if not prov_res.fetchone():
            raise HTTPException(status_code=404, detail="Provider not found")

        # Verify appointment type exists
        at_res = db.execute(text("SELECT id FROM appointment_types WHERE id = :atid"), {"atid": appointment_type_id})
        if not at_res.fetchone():
            raise HTTPException(status_code=404, detail="Appointment type not found")

        # Check if mapping exists
        map_res = db.execute(
            text("SELECT is_enabled FROM provider_appointment_types WHERE provider_id = :pid AND appointment_type_id = :atid"),
            {"pid": provider_id, "atid": appointment_type_id}
        )
        row = map_res.fetchone()

        if row:
            # Update existing record to enabled
            db.execute(
                text("UPDATE provider_appointment_types SET is_enabled = 1 WHERE provider_id = :pid AND appointment_type_id = :atid"),
                {"pid": provider_id, "atid": appointment_type_id}
            )
        else:
            # Insert new record
            db.execute(
                text("INSERT INTO provider_appointment_types (provider_id, appointment_type_id, is_enabled) VALUES (:pid, :atid, 1)"),
                {"pid": provider_id, "atid": appointment_type_id}
            )
        
        db.commit()
        return SuccessResponse(success=True, message=f"Enabled appointment type {appointment_type_id} for provider {provider_id}")
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.get("/providers/{provider_id}/appointment-types", response_model=List[ProviderAppointmentTypeResponse], operation_id="get_provider_appointment_types")
def get_provider_appointment_types(
    provider_id: int = Path(..., description="The ID of the provider")
):
    """
    Retrieve all appointment types associated with the given provider, indicating whether each is enabled or disabled.
    """
    db = SessionLocal()
    try:
        # Verify provider exists
        prov_res = db.execute(text("SELECT id FROM providers WHERE id = :pid"), {"pid": provider_id})
        if not prov_res.fetchone():
            raise HTTPException(status_code=404, detail="Provider not found")

        query = text("""
            SELECT 
                at.id, 
                at.name, 
                at.description, 
                COALESCE(pat.is_enabled, 0) as is_enabled
            FROM appointment_types at
            LEFT JOIN provider_appointment_types pat ON at.id = pat.appointment_type_id AND pat.provider_id = :pid
            ORDER BY at.id
        """)
        
        results = db.execute(query, {"pid": provider_id}).fetchall()
        
        response_list = []
        for r in results:
            response_list.append(ProviderAppointmentTypeResponse(
                id=r[0],
                name=r[1],
                description=r[2] if r[2] is not None else "",
                is_enabled=bool(r[3])
            ))
            
        return response_list
    finally:
        db.close()


@app.get("/appointment-types/default", response_model=Optional[AppointmentTypeResponse], operation_id="get_default_appointment_type")
def get_default_appointment_type():
    """
    Retrieve the currently set default appointment type for new patients.
    """
    db = SessionLocal()
    try:
        query = text("""
            SELECT id, name, description 
            FROM appointment_types 
            WHERE is_default_for_new_patients = 1
            LIMIT 1
        """)
        result = db.execute(query).fetchone()
        
        if not result:
            return None
            
        return AppointmentTypeResponse(
            id=result[0],
            name=result[1],
            description=result[2] if result[2] is not None else ""
        )
    finally:
        db.close()
