import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from typing import List, Optional

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PrescriptionCreate(BaseModel):
    medication_id: int
    dose: str
    frequency: str
    duration_days: int


class SoapNoteCreate(BaseModel):
    subjective: Optional[str] = None
    objective: Optional[str] = None
    assessment: Optional[str] = None
    plan: Optional[str] = None
    diagnosis_codes: Optional[List[str]] = []
    billing_codes: Optional[List[str]] = []


@app.post("/video-visits/{visit_id}/prescriptions", operation_id="create_prescription")
def create_prescription(
    visit_id: int = Path(..., description="The ID of the video visit"),
    body: PrescriptionCreate = ...,
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with engine.connect() as conn:
        # Verify visit exists and belongs to a valid state (optional but good practice)
        visit_check = conn.execute(text("SELECT id FROM video_visits WHERE id = :vid"), {"vid": visit_id}).fetchone()
        if not visit_check:
            raise HTTPException(status_code=404, detail="Video visit not found")

        # Verify medication exists
        med_check = conn.execute(text("SELECT id FROM medications WHERE id = :mid"), {"mid": body.medication_id}).fetchone()
        if not med_check:
            raise HTTPException(status_code=404, detail="Medication not found")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_stmt = text("""
            INSERT INTO prescriptions 
            (visit_id, medication_id, dose, frequency, duration_days, status, prescribed_by, created_at, updated_at)
            VALUES 
            (:visit_id, :medication_id, :dose, :frequency, :duration_days, 'active', :prescribed_by, :now, :now)
        """)
        
        result = conn.execute(insert_stmt, {
            "visit_id": visit_id,
            "medication_id": body.medication_id,
            "dose": body.dose,
            "frequency": body.frequency,
            "duration_days": body.duration_days,
            "prescribed_by": current_user_id,
            "now": now
        })
        
        new_id = result.lastrowid
        
        # Fetch the created record to return full details including defaults
        fetch_stmt = text("""
            SELECT id, visit_id, medication_id, dose, frequency, duration_days, status, prescribed_by, created_at, updated_at
            FROM prescriptions
            WHERE id = :id
        """)
        row = conn.execute(fetch_stmt, {"id": new_id}).fetchone()
        
        conn.commit()

        return {
            "id": row.id,
            "visit_id": row.visit_id,
            "medication_id": row.medication_id,
            "dose": row.dose,
            "frequency": row.frequency,
            "duration_days": row.duration_days,
            "status": row.status,
            "prescribed_by": row.prescribed_by,
            "created_at": row.created_at,
            "updated_at": row.updated_at
        }


@app.post("/video-visits/{visit_id}/soap-notes", operation_id="create_soap_note")
def create_soap_note(
    visit_id: int = Path(..., description="The ID of the video visit"),
    body: SoapNoteCreate = ...,
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with engine.connect() as conn:
        # Verify visit exists
        visit_check = conn.execute(text("SELECT id FROM video_visits WHERE id = :vid"), {"vid": visit_id}).fetchone()
        if not visit_check:
            raise HTTPException(status_code=404, detail="Video visit not found")

        # Check if SOAP note already exists for this visit (UNIQUE constraint on visit_id in soap_notes)
        existing_note = conn.execute(text("SELECT id FROM soap_notes WHERE visit_id = :vid"), {"vid": visit_id}).fetchone()
        if existing_note:
            raise HTTPException(status_code=409, detail="SOAP note already exists for this visit")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Insert SOAP Note
        insert_note_stmt = text("""
            INSERT INTO soap_notes 
            (visit_id, subjective, objective, assessment, plan, created_at, updated_at)
            VALUES 
            (:visit_id, :subjective, :objective, :assessment, :plan, :now, :now)
        """)
        
        note_result = conn.execute(insert_note_stmt, {
            "visit_id": visit_id,
            "subjective": body.subjective,
            "objective": body.objective,
            "assessment": body.assessment,
            "plan": body.plan,
            "now": now
        })
        
        soap_note_id = note_result.lastrowid

        # Handle Diagnosis Codes
        if body.diagnosis_codes:
            for code in body.diagnosis_codes:
                # Validate code exists
                diag_exists = conn.execute(text("SELECT code FROM diagnosis_codes WHERE code = :code"), {"code": code}).fetchone()
                if not diag_exists:
                    raise HTTPException(status_code=400, detail=f"Diagnosis code {code} does not exist")
                
                # Insert association
                conn.execute(text("""
                    INSERT OR IGNORE INTO soap_note_diagnoses (soap_note_id, diagnosis_code, created_at)
                    VALUES (:sid, :dc, :now)
                """), {"sid": soap_note_id, "dc": code, "now": now})

        # Handle Billing Codes
        if body.billing_codes:
            for code in body.billing_codes:
                # Validate code exists
                bill_exists = conn.execute(text("SELECT code FROM billing_codes WHERE code = :code"), {"code": code}).fetchone()
                if not bill_exists:
                    raise HTTPException(status_code=400, detail=f"Billing code {code} does not exist")
                
                # Insert association
                conn.execute(text("""
                    INSERT OR IGNORE INTO soap_note_billing (soap_note_id, billing_code, created_at)
                    VALUES (:sid, :bc, :now)
                """), {"sid": soap_note_id, "bc": code, "now": now})

        # Fetch the created note to return
        fetch_stmt = text("""
            SELECT id, visit_id, subjective, objective, assessment, plan, created_at, updated_at
            FROM soap_notes
            WHERE id = :id
        """)
        row = conn.execute(fetch_stmt, {"id": soap_note_id}).fetchone()
        
        conn.commit()

        return {
            "id": row.id,
            "visit_id": row.visit_id,
            "subjective": row.subjective,
            "objective": row.objective,
            "assessment": row.assessment,
            "plan": row.plan,
            "created_at": row.created_at,
            "updated_at": row.updated_at
        }
