import os
from datetime import datetime, date
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="ClinicFlow API")


# --- Pydantic Models for Responses ---

class PatientResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    date_of_birth: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

    class Config:
        from_attributes = True


class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    appointment_date: str
    appointment_time: Optional[str] = None
    provider_name: Optional[str] = None
    status: str

    class Config:
        from_attributes = True


class PatientInsuranceResponse(BaseModel):
    id: int
    patient_id: int
    insurance_plan_id: int
    member_id: str
    group_number: Optional[str] = None
    is_primary: int
    effective_date: Optional[str] = None
    termination_date: Optional[str] = None

    class Config:
        from_attributes = True


class EligibilityCheckCreateRequest(BaseModel):
    patient_id: int
    appointment_id: int
    patient_insurance_id: int
    checked_by_user_id: int
    check_date: str
    response: str = Field(..., pattern=r"^(eligible|ineligible|pending|error)$")
    response_details: Optional[str] = None


class EligibilityCheckResponse(BaseModel):
    id: int
    patient_id: int
    appointment_id: Optional[int] = None
    patient_insurance_id: Optional[int] = None
    checked_by_user_id: int
    check_date: str
    response: str
    response_details: Optional[str] = None

    class Config:
        from_attributes = True


# --- Endpoints ---

@app.get("/patients/{patient_id}", response_model=PatientResponse, operation_id="get_patient")
def get_patient(patient_id: int = Path(..., description="Internal identifier for the patient")):
    db = SessionLocal()
    try:
        result = db.execute(text("""
            SELECT id, first_name, last_name, date_of_birth, phone, email 
            FROM patients 
            WHERE id = :pid
        """), {"pid": patient_id}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Patient not found")
            
        return {
            "id": result[0],
            "first_name": result[1],
            "last_name": result[2],
            "date_of_birth": result[3],
            "phone": result[4],
            "email": result[5]
        }
    finally:
        db.close()


@app.get("/appointments", response_model=List[AppointmentResponse], operation_id="find_appointments")
def find_appointments(
    patient_id: int = Query(..., description="Internal identifier for the patient"),
    appointment_date: str = Query(..., description="Date of the appointment in YYYY-MM-DD format")
):
    db = SessionLocal()
    try:
        results = db.execute(text("""
            SELECT id, patient_id, appointment_date, appointment_time, provider_name, status 
            FROM appointments 
            WHERE patient_id = :pid AND appointment_date = :adate
        """), {"pid": patient_id, "adate": appointment_date}).fetchall()
        
        return [
            {
                "id": r[0],
                "patient_id": r[1],
                "appointment_date": r[2],
                "appointment_time": r[3],
                "provider_name": r[4],
                "status": r[5]
            }
            for r in results
        ]
    finally:
        db.close()


@app.get("/patient_insurance", response_model=PatientInsuranceResponse, operation_id="get_patient_insurance")
def get_patient_insurance(
    patient_id: int = Query(..., description="Internal identifier for the patient")
):
    db = SessionLocal()
    try:
        # Retrieve primary insurance (is_primary = 1)
        result = db.execute(text("""
            SELECT id, patient_id, insurance_plan_id, member_id, group_number, is_primary, effective_date, termination_date 
            FROM patient_insurance 
            WHERE patient_id = :pid AND is_primary = 1
        """), {"pid": patient_id}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Primary insurance not found for patient")
            
        return {
            "id": result[0],
            "patient_id": result[1],
            "insurance_plan_id": result[2],
            "member_id": result[3],
            "group_number": result[4],
            "is_primary": result[5],
            "effective_date": result[6],
            "termination_date": result[7]
        }
    finally:
        db.close()


@app.post("/eligibility_checks", response_model=EligibilityCheckResponse, operation_id="record_eligibility_check")
def record_eligibility_check(payload: EligibilityCheckCreateRequest):
    db = SessionLocal()
    try:
        # Validate that referenced entities exist to avoid FK violations later or provide better errors
        # Check Patient
        p_exists = db.execute(text("SELECT 1 FROM patients WHERE id = :pid"), {"pid": payload.patient_id}).fetchone()
        if not p_exists:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        # Check Appointment
        a_exists = db.execute(text("SELECT 1 FROM appointments WHERE id = :aid"), {"aid": payload.appointment_id}).fetchone()
        if not a_exists:
            raise HTTPException(status_code=404, detail="Appointment not found")
            
        # Check Patient Insurance
        pi_exists = db.execute(text("SELECT 1 FROM patient_insurance WHERE id = :piid"), {"piid": payload.patient_insurance_id}).fetchone()
        if not pi_exists:
            raise HTTPException(status_code=404, detail="Patient insurance record not found")

        # Insert new eligibility check
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
        res = db.execute(text("""
            INSERT INTO eligibility_checks (
                patient_id, 
                appointment_id, 
                patient_insurance_id, 
                checked_by_user_id, 
                check_date, 
                response, 
                response_details,
                created_at,
                updated_at
            ) VALUES (
                :patient_id, 
                :appointment_id, 
                :patient_insurance_id, 
                :checked_by_user_id, 
                :check_date, 
                :response, 
                :response_details,
                :now,
                :now
            ) RETURNING id
        """), {
            "patient_id": payload.patient_id,
            "appointment_id": payload.appointment_id,
            "patient_insurance_id": payload.patient_insurance_id,
            "checked_by_user_id": payload.checked_by_user_id,
            "check_date": payload.check_date,
            "response": payload.response,
            "response_details": payload.response_details,
            "now": now_str
        })
        
        new_id = res.fetchone()[0]
        db.commit()
        
        # Fetch back the inserted row to ensure consistency with defaults/triggers if any
        final_row = db.execute(text("""
            SELECT id, patient_id, appointment_id, patient_insurance_id, checked_by_user_id, check_date, response, response_details 
            FROM eligibility_checks 
            WHERE id = :eid
        """), {"eid": new_id}).fetchone()
        
        return {
            "id": final_row[0],
            "patient_id": final_row[1],
            "appointment_id": final_row[2],
            "patient_insurance_id": final_row[3],
            "checked_by_user_id": final_row[4],
            "check_date": final_row[5],
            "response": final_row[6],
            "response_details": final_row[7]
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
