import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models for Requests ---

class CreateVisitRequest(BaseModel):
    clinician_id: int
    visit_type: Optional[str] = "follow-up"
    chief_complaint: Optional[str] = None
    notes: Optional[str] = None


class AssignDiagnosisRequest(BaseModel):
    diagnosis_code: str
    is_primary: bool = False


class RecordROMRequest(BaseModel):
    joint: str
    movement: str
    degrees: float
    side: Optional[str] = None


class RecordPainAssessmentRequest(BaseModel):
    pain_scale: int
    location: Optional[str] = None
    notes: Optional[str] = None


class CreatePrescriptionRequest(BaseModel):
    prescription_type: str
    description: str
    instructions: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class CreateFollowUpPlanRequest(BaseModel):
    plan_date: str
    interval_months: Optional[int] = None
    description: str


class CreateReferralRequest(BaseModel):
    specialty: str
    reason: str
    urgency: Optional[str] = "routine"


# --- Helper Functions ---

def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        # Fallback or error handling depending on strictness. 
        # Assuming runtime always provides it as per prompt instructions.
        raise HTTPException(status_code=401, detail="Current user ID not found in environment")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid Current User ID format")


# --- Endpoints ---

@app.post("/patients/{patient_id}/visits", operation_id="create_patient_visit")
def create_patient_visit(patient_id: int, payload: CreateVisitRequest):
    current_user_id = get_current_user_id()
    
    # Verify patient exists
    with engine.connect() as conn:
        res = conn.execute(text("SELECT id FROM patients WHERE id = :pid"), {"pid": patient_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Patient not found")
        
        # Verify clinician exists
        res = conn.execute(text("SELECT id FROM users WHERE id = :cid"), {"cid": payload.clinician_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Clinician not found")

        insert_stmt = text("""
            INSERT INTO visits (patient_id, clinician_id, visit_type, chief_complaint, notes, created_at, updated_at)
            VALUES (:patient_id, :clinician_id, :visit_type, :chief_complaint, :notes, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        result = conn.execute(insert_stmt, {
            "patient_id": patient_id,
            "clinician_id": payload.clinician_id,
            "visit_type": payload.visit_type,
            "chief_complaint": payload.chief_complaint,
            "notes": payload.notes
        })
        visit_id = result.lastrowid
        conn.commit()
        
    return {"visit_id": visit_id}


@app.post("/visits/{visit_id}/diagnoses", operation_id="assign_diagnosis_to_visit")
def assign_diagnosis_to_visit(visit_id: int, payload: AssignDiagnosisRequest):
    with engine.connect() as conn:
        # Verify visit exists
        res = conn.execute(text("SELECT id FROM visits WHERE id = :vid"), {"vid": visit_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Visit not found")
        
        # Find diagnosis ID by code
        res = conn.execute(text("SELECT id FROM diagnoses WHERE icd10_code = :code"), {"code": payload.diagnosis_code})
        diag_row = res.fetchone()
        if not diag_row:
            raise HTTPException(status_code=404, detail="Diagnosis code not found")
        
        diagnosis_id = diag_row[0]
        
        # Check unique constraint manually to provide better error if needed, though DB will enforce
        # Insert into visit_diagnoses
        insert_stmt = text("""
            INSERT INTO visit_diagnoses (visit_id, diagnosis_id, is_primary, created_at)
            VALUES (:visit_id, :diagnosis_id, :is_primary, CURRENT_TIMESTAMP)
        """)
        try:
            result = conn.execute(insert_stmt, {
                "visit_id": visit_id,
                "diagnosis_id": diagnosis_id,
                "is_primary": 1 if payload.is_primary else 0
            })
            assignment_id = result.lastrowid
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail=f"Failed to assign diagnosis: {str(e)}")
            
    return {"assignment_id": assignment_id}


@app.post("/visits/{visit_id}/range_of_motion", operation_id="record_range_of_motion")
def record_range_of_motion(visit_id: int, payload: RecordROMRequest):
    with engine.connect() as conn:
        # Verify visit exists
        res = conn.execute(text("SELECT id FROM visits WHERE id = :vid"), {"vid": visit_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Visit not found")
        
        insert_stmt = text("""
            INSERT INTO range_of_motion (visit_id, joint, movement, degrees, side, created_at)
            VALUES (:visit_id, :joint, :movement, :degrees, :side, CURRENT_TIMESTAMP)
        """)
        result = conn.execute(insert_stmt, {
            "visit_id": visit_id,
            "joint": payload.joint,
            "movement": payload.movement,
            "degrees": payload.degrees,
            "side": payload.side
        })
        measurement_id = result.lastrowid
        conn.commit()
        
    return {"measurement_id": measurement_id}


@app.post("/visits/{visit_id}/pain_assessment", operation_id="record_pain_assessment")
def record_pain_assessment(visit_id: int, payload: RecordPainAssessmentRequest):
    if payload.pain_scale < 0 or payload.pain_scale > 10:
        raise HTTPException(status_code=400, detail="Pain scale must be between 0 and 10")
        
    with engine.connect() as conn:
        # Verify visit exists
        res = conn.execute(text("SELECT id FROM visits WHERE id = :vid"), {"vid": visit_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Visit not found")
        
        insert_stmt = text("""
            INSERT INTO pain_assessments (visit_id, pain_scale, location, notes, created_at)
            VALUES (:visit_id, :pain_scale, :location, :notes, CURRENT_TIMESTAMP)
        """)
        result = conn.execute(insert_stmt, {
            "visit_id": visit_id,
            "pain_scale": payload.pain_scale,
            "location": payload.location,
            "notes": payload.notes
        })
        assessment_id = result.lastrowid
        conn.commit()
        
    return {"assessment_id": assessment_id}


@app.post("/visits/{visit_id}/prescriptions", operation_id="create_prescription")
def create_prescription(visit_id: int, payload: CreatePrescriptionRequest):
    current_user_id = get_current_user_id()
    
    with engine.connect() as conn:
        # Get visit details including patient_id
        res = conn.execute(text("SELECT patient_id FROM visits WHERE id = :vid"), {"vid": visit_id})
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Visit not found")
        
        patient_id = row[0]
        
        # Determine prescriber_id: use current user if they are a clinician, otherwise default logic?
        # The spec says "creates a prescription entry...". Usually the person creating it is the prescriber.
        # We assume AWM_CURRENT_USER_ID is the prescriber.
        prescriber_id = current_user_id
        
        # Validate prescriber exists and is active/valid? Just check existence.
        res = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": prescriber_id})
        if not res.fetchone():
             raise HTTPException(status_code=403, detail="Invalid prescriber context")

        insert_stmt = text("""
            INSERT INTO prescriptions (visit_id, patient_id, prescriber_id, prescription_type, description, instructions, start_date, end_date, status, created_at, updated_at)
            VALUES (:visit_id, :patient_id, :prescriber_id, :prescription_type, :description, :instructions, :start_date, :end_date, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        result = conn.execute(insert_stmt, {
            "visit_id": visit_id,
            "patient_id": patient_id,
            "prescriber_id": prescriber_id,
            "prescription_type": payload.prescription_type,
            "description": payload.description,
            "instructions": payload.instructions,
            "start_date": payload.start_date,
            "end_date": payload.end_date
        })
        prescription_id = result.lastrowid
        conn.commit()
        
    return {"prescription_id": prescription_id}


@app.post("/visits/{visit_id}/follow_up_plans", operation_id="create_follow_up_plan")
def create_follow_up_plan(visit_id: int, payload: CreateFollowUpPlanRequest):
    with engine.connect() as conn:
        # Get visit details including patient_id
        res = conn.execute(text("SELECT patient_id FROM visits WHERE id = :vid"), {"vid": visit_id})
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Visit not found")
        
        patient_id = row[0]
        
        insert_stmt = text("""
            INSERT INTO follow_up_plans (visit_id, patient_id, plan_date, interval_months, description, status, created_at, updated_at)
            VALUES (:visit_id, :patient_id, :plan_date, :interval_months, :description, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        result = conn.execute(insert_stmt, {
            "visit_id": visit_id,
            "patient_id": patient_id,
            "plan_date": payload.plan_date,
            "interval_months": payload.interval_months,
            "description": payload.description
        })
        plan_id = result.lastrowid
        conn.commit()
        
    return {"plan_id": plan_id}


@app.post("/visits/{visit_id}/referrals", operation_id="create_referral")
def create_referral(visit_id: int, payload: CreateReferralRequest):
    current_user_id = get_current_user_id()
    
    with engine.connect() as conn:
        # Get visit details including patient_id
        res = conn.execute(text("SELECT patient_id FROM visits WHERE id = :vid"), {"vid": visit_id})
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Visit not found")
        
        patient_id = row[0]
        
        # Referring provider is the current user
        referring_provider_id = current_user_id
        
        # Validate provider exists
        res = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": referring_provider_id})
        if not res.fetchone():
             raise HTTPException(status_code=403, detail="Invalid referring provider context")

        insert_stmt = text("""
            INSERT INTO referrals (visit_id, patient_id, referring_provider_id, specialty, reason, urgency, status, created_at, updated_at)
            VALUES (:visit_id, :patient_id, :referring_provider_id, :specialty, :reason, :urgency, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        result = conn.execute(insert_stmt, {
            "visit_id": visit_id,
            "patient_id": patient_id,
            "referring_provider_id": referring_provider_id,
            "specialty": payload.specialty,
            "reason": payload.reason,
            "urgency": payload.urgency
        })
        referral_id = result.lastrowid
        conn.commit()
        
    return {"referral_id": referral_id}
