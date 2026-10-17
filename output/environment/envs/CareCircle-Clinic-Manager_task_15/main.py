import os
from datetime import date, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CreateVisitRequest(BaseModel):
    patient_id: int
    clinician_id: int


class VisitResponse(BaseModel):
    visit_id: int


class SoapNoteRequest(BaseModel):
    visit_id: int
    subjective: str
    objective: str
    assessment: str
    plan: str


class NoteResponse(BaseModel):
    note_id: int


class VitalsRequest(BaseModel):
    visit_id: int
    systolic_bp: int
    diastolic_bp: int
    heart_rate: int


class VitalResponse(BaseModel):
    vital_id: int


class DiagnosisAssignmentRequest(BaseModel):
    visit_id: int
    diagnosis_code: str


class AssignmentResponse(BaseModel):
    assignment_id: int


class DiagnosisCodeItem(BaseModel):
    code: str
    description: str


@app.post("/visits", response_model=VisitResponse, operation_id="create_followup_visit")
def create_followup_visit(payload: CreateVisitRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Current user not identified")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")

    # Verify patient exists
    with engine.connect() as conn:
        res = conn.execute(
            text("SELECT id FROM patients WHERE id = :pid"),
            {"pid": payload.patient_id}
        ).fetchone()
        if not res:
            raise HTTPException(status_code=404, detail="Patient not found")

        # Verify clinician exists and is a valid user (optional check based on role, but spec says assign to current clinician)
        # The request body has clinician_id. We should verify it exists in users table.
        res_clinician = conn.execute(
            text("SELECT id FROM users WHERE id = :cid"),
            {"cid": payload.clinician_id}
        ).fetchone()
        if not res_clinician:
            raise HTTPException(status_code=404, detail="Clinician not found")

        today = date.today().isoformat()
        
        result = conn.execute(
            text("""
                INSERT INTO visits (patient_id, clinician_id, visit_date, visit_type, created_at, updated_at)
                VALUES (:pid, :cid, :vdate, 'follow-up', :now, :now)
            """),
            {
                "pid": payload.patient_id,
                "cid": payload.clinician_id,
                "vdate": today,
                "now": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            }
        )
        conn.commit()
        visit_id = result.lastrowid
        
    return VisitResponse(visit_id=visit_id)


@app.post("/soap_notes", response_model=NoteResponse, operation_id="add_soap_note")
def add_soap_note(payload: SoapNoteRequest):
    with engine.connect() as conn:
        # Verify visit exists
        res = conn.execute(
            text("SELECT id FROM visits WHERE id = :vid"),
            {"vid": payload.visit_id}
        ).fetchone()
        if not res:
            raise HTTPException(status_code=404, detail="Visit not found")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text("""
                INSERT INTO soap_notes (visit_id, subjective, objective, assessment, plan, created_at, updated_at)
                VALUES (:vid, :subj, :obj, :assess, :plan, :now, :now)
            """),
            {
                "vid": payload.visit_id,
                "subj": payload.subjective,
                "obj": payload.objective,
                "assess": payload.assessment,
                "plan": payload.plan,
                "now": now
            }
        )
        conn.commit()
        note_id = result.lastrowid
        
    return NoteResponse(note_id=note_id)


@app.post("/vitals", response_model=VitalResponse, operation_id="record_vitals")
def record_vitals(payload: VitalsRequest):
    with engine.connect() as conn:
        # Verify visit exists
        res = conn.execute(
            text("SELECT id FROM visits WHERE id = :vid"),
            {"vid": payload.visit_id}
        ).fetchone()
        if not res:
            raise HTTPException(status_code=404, detail="Visit not found")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text("""
                INSERT INTO vitals (visit_id, systolic_bp, diastolic_bp, heart_rate, recorded_at, created_at, updated_at)
                VALUES (:vid, :sys, :dia, :hr, :rec_time, :now, :now)
            """),
            {
                "vid": payload.visit_id,
                "sys": payload.systolic_bp,
                "dia": payload.diastolic_bp,
                "hr": payload.heart_rate,
                "rec_time": now,
                "now": now
            }
        )
        conn.commit()
        vital_id = result.lastrowid
        
    return VitalResponse(vital_id=vital_id)


@app.post("/visit_diagnoses", response_model=AssignmentResponse, operation_id="assign_diagnosis")
def assign_diagnosis(payload: DiagnosisAssignmentRequest):
    with engine.connect() as conn:
        # Verify visit exists
        res_visit = conn.execute(
            text("SELECT id FROM visits WHERE id = :vid"),
            {"vid": payload.visit_id}
        ).fetchone()
        if not res_visit:
            raise HTTPException(status_code=404, detail="Visit not found")

        # Verify diagnosis code exists
        res_code = conn.execute(
            text("SELECT code FROM diagnosis_codes WHERE code = :code"),
            {"code": payload.diagnosis_code}
        ).fetchone()
        if not res_code:
            raise HTTPException(status_code=404, detail="Diagnosis code not found")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        try:
            result = conn.execute(
                text("""
                    INSERT INTO visit_diagnoses (visit_id, diagnosis_code, created_at)
                    VALUES (:vid, :code, :now)
                """),
                {
                    "vid": payload.visit_id,
                    "code": payload.diagnosis_code,
                    "now": now
                }
            )
            conn.commit()
            assignment_id = result.lastrowid
        except Exception as e:
            # Handle unique constraint violation if necessary, though spec doesn't explicitly forbid duplicates 
            # but DB schema has UNIQUE (visit_id, diagnosis_code). 
            # If duplicate, we might want to return error or existing ID? Spec implies creation.
            # Let's assume standard insert failure raises 400 or similar if logic dictates, 
            # but typically for this task structure, simple insert is expected.
            raise HTTPException(status_code=400, detail=f"Failed to assign diagnosis: {str(e)}")
            
    return AssignmentResponse(assignment_id=assignment_id)


@app.get("/diagnosis_codes", response_model=list[DiagnosisCodeItem], operation_id="list_diagnosis_codes")
def list_diagnosis_codes():
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT code, description FROM diagnosis_codes ORDER BY code")
        ).fetchall()
        
    items = [
        DiagnosisCodeItem(code=row.code, description=row.description)
        for row in rows
    ]
    return items
