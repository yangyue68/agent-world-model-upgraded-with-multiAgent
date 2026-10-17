import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from typing import Optional

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CareCircle Clinic Manager API")


class PatientCreate(BaseModel):
    first_name: str = Field(..., description="Patient's first name")
    last_name: str = Field(..., description="Patient's last name")
    date_of_birth: str = Field(..., description="Patient's date of birth in YYYY-MM-DD format")
    phone: Optional[str] = Field(None, description="Patient's phone number")
    email: Optional[str] = Field(None, description="Patient's email address")
    address: Optional[str] = Field(None, description="Patient's full address")
    insurance_provider: Optional[str] = Field(None, description="Name of the insurance provider")
    policy_number: Optional[str] = Field(None, description="Insurance policy number")
    contact_preference: Optional[str] = Field(
        None, 
        description="Preferred method of contact (email, phone, mail)"
    )


class PatientResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    date_of_birth: str
    phone: Optional[str]
    email: Optional[str]
    address: Optional[str]
    insurance_provider: Optional[str]
    policy_number: Optional[str]
    contact_preference: Optional[str]
    created_at: str
    updated_at: str


@app.post("/patients", response_model=PatientResponse, operation_id="create_patient")
def create_patient(patient_data: PatientCreate):
    """
    Creates a new patient profile with the provided details including name, 
    date of birth, contact information, address, insurance details, and preferred contact method.
    """
    
    # Validate contact_preference against allowed values if provided
    if patient_data.contact_preference is not None:
        allowed_preferences = {'email', 'phone', 'mail'}
        if patient_data.contact_preference not in allowed_preferences:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid contact_preference. Must be one of {allowed_preferences}"
            )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    insert_query = text("""
        INSERT INTO patients (
            first_name, 
            last_name, 
            date_of_birth, 
            phone, 
            email, 
            address, 
            insurance_provider, 
            policy_number, 
            contact_preference,
            created_at,
            updated_at
        ) VALUES (
            :first_name, 
            :last_name, 
            :date_of_birth, 
            :phone, 
            :email, 
            :address, 
            :insurance_provider, 
            :policy_number, 
            :contact_preference,
            :created_at,
            :updated_at
        )
    """)

    params = {
        "first_name": patient_data.first_name,
        "last_name": patient_data.last_name,
        "date_of_birth": patient_data.date_of_birth,
        "phone": patient_data.phone,
        "email": patient_data.email,
        "address": patient_data.address,
        "insurance_provider": patient_data.insurance_provider,
        "policy_number": patient_data.policy_number,
        "contact_preference": patient_data.contact_preference,
        "created_at": now,
        "updated_at": now
    }

    try:
        with engine.begin() as conn:
            result = conn.execute(insert_query, params)
            new_id = result.lastrowid
            
            # Fetch the newly created record to return accurate data including defaults
            select_query = text("""
                SELECT 
                    id, 
                    first_name, 
                    last_name, 
                    date_of_birth, 
                    phone, 
                    email, 
                    address, 
                    insurance_provider, 
                    policy_number, 
                    contact_preference,
                    created_at,
                    updated_at
                FROM patients 
                WHERE id = :id
            """)
            
            row = conn.execute(select_query, {"id": new_id}).fetchone()
            
            if not row:
                raise HTTPException(status_code=500, detail="Failed to retrieve created patient")

            return PatientResponse(
                id=row.id,
                first_name=row.first_name,
                last_name=row.last_name,
                date_of_birth=row.date_of_birth,
                phone=row.phone,
                email=row.email,
                address=row.address,
                insurance_provider=row.insurance_provider,
                policy_number=row.policy_number,
                contact_preference=row.contact_preference,
                created_at=str(row.created_at),
                updated_at=str(row.updated_at)
            )
            
    except Exception as e:
        # Log error internally but return generic message or specific validation errors if needed
        # For this task, we assume standard DB operations succeed unless constraint violated
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
