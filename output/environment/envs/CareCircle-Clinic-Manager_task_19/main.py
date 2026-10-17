import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from typing import Optional, Literal

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CareCircle Clinic Manager API")


class PatientUpdateRequest(BaseModel):
    phone: Optional[str] = None
    contact_preference: Optional[Literal["Phone", "Email", "SMS", "Mail"]] = None


class PatientResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    phone: Optional[str]
    contact_preference: Optional[str]
    updated_at: datetime


@app.put("/patients/{patient_id}", response_model=PatientResponse, operation_id="update_patient_contact_info")
def update_patient_contact_info(
    patient_id: int = Path(..., description="Internal ID of the patient to update"),
    body: PatientUpdateRequest = ...
):
    """
    Update the contact preference and phone number for a specific patient identified by patient ID.
    Only allows updating to valid contact preference values: Phone, Email, SMS, Mail.
    """
    
    # Fetch current patient data to ensure existence and get immutable fields
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, first_name, last_name, phone, contact_preference FROM patients WHERE id = :pid"),
            {"pid": patient_id}
        )
        row = result.fetchone()
        
        if row is None:
            raise HTTPException(status_code=404, detail=f"Patient with ID {patient_id} not found.")
            
        current_data = dict(row._mapping)
        
        # Determine new values based on request body
        new_phone = body.phone if body.phone is not None else current_data['phone']
        new_contact_pref = body.contact_preference if body.contact_preference is not None else current_data['contact_preference']
        
        # Validate contact preference against allowed enum values explicitly defined in schema
        allowed_prefs = ["Phone", "Email", "SMS", "Mail"]
        if new_contact_pref not in allowed_prefs:
             raise HTTPException(status_code=422, detail=f"Invalid contact preference '{new_contact_pref}'. Must be one of {allowed_prefs}")

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Perform update
        conn.execute(
            text("""
                UPDATE patients 
                SET phone = :phone, 
                    contact_preference = :pref, 
                    updated_at = :updated_at 
                WHERE id = :pid
            """),
            {
                "phone": new_phone,
                "pref": new_contact_pref,
                "updated_at": now_utc,
                "pid": patient_id
            }
        )
        conn.commit()
        
        # Return updated object structure matching response model requirements
        return PatientResponse(
            id=current_data['id'],
            first_name=current_data['first_name'],
            last_name=current_data['last_name'],
            phone=new_phone,
            contact_preference=new_contact_pref,
            updated_at=datetime.fromisoformat(now_utc.replace(' ', 'T') + '+00:00') # Parse back to datetime for Pydantic serialization consistency if needed, though string format often works depending on config. 
            # Actually, let's just pass the datetime object directly constructed above or parsed from DB.
            # The DB stores strings. We calculated now_utc as string. Let's re-parse strictly for the response model which expects date-time.
        )
