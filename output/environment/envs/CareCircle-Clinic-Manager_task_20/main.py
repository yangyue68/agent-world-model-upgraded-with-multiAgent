import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class InsuranceDetail(BaseModel):
    id: int
    provider_name: str
    policy_number: str
    group_number: Optional[str]
    plan_type: Optional[str]
    effective_date: Optional[str]
    termination_date: Optional[str]
    is_primary: int


class PatientProfileResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    date_of_birth: str
    gender: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    address_line1: Optional[str]
    address_line2: Optional[str]
    city: Optional[str]
    state: Optional[str]
    postal_code: Optional[str]
    country: Optional[str]
    preferred_contact_method: Optional[str]
    preferred_language: Optional[str]
    preferred_phone: Optional[str]
    preferred_email: Optional[str]
    best_time_to_call: Optional[str]
    do_not_disturb: int
    insurance_details: List[InsuranceDetail]


@app.get("/patients/{patient_id}", response_model=PatientProfileResponse, operation_id="get_patient_profile")
def get_patient_profile(patient_id: int):
    """
    Retrieve complete patient profile including demographics, insurance, and contact preferences.
    """
    
    # Fetch patient demographics and contact info
    query_patient = text("""
        SELECT 
            id, 
            first_name, 
            last_name, 
            date_of_birth, 
            gender, 
            phone, 
            email, 
            address_line1, 
            address_line2, 
            city, 
            state, 
            postal_code, 
            country, 
            preferred_contact_method, 
            preferred_language, 
            preferred_phone, 
            preferred_email, 
            best_time_to_call, 
            do_not_disturb
        FROM patients
        WHERE id = :pid
    """)
    
    with engine.connect() as conn:
        result_patient = conn.execute(query_patient, {"pid": patient_id}).fetchone()
        
        if not result_patient:
            raise HTTPException(status_code=404, detail="Patient not found")
            
        # Convert row to dict for easier handling using _mapping attribute
        patient_data = dict(result_patient._mapping)
        
        # Fetch insurance details
        query_insurance = text("""
            SELECT 
                id, 
                provider_name, 
                policy_number, 
                group_number, 
                plan_type, 
                effective_date, 
                termination_date, 
                is_primary
            FROM insurance_policies
            WHERE patient_id = :pid
        """)
        
        result_insurance = conn.execute(query_insurance, {"pid": patient_id}).fetchall()
        
        insurance_list = []
        for row in result_insurance:
            ins_dict = dict(row._mapping)
            insurance_list.append(InsuranceDetail(**ins_dict))
            
        # Construct final response object
        response_obj = PatientProfileResponse(
            **patient_data,
            insurance_details=insurance_list
        )
        
        return response_obj
