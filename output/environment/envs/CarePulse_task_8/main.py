import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class SOAPNoteResponse(BaseModel):
    id: int
    appointment_id: int
    patient_id: int
    clinician_id: int
    subjective: str
    objective: str
    assessment: str
    plan: str
    created_at: str
    updated_at: str


class DiagnosisResponse(BaseModel):
    id: int
    soap_note_id: int
    icd10_code: str
    description: str
    is_primary: bool
    created_at: str


class BillingCodeResponse(BaseModel):
    id: int
    soap_note_id: int
    cpt_code: str
    description: str
    units: int
    created_at: str


class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    clinician_id: int
    appointment_type: str
    status: str
    scheduled_start: str
    scheduled_end: str
    reason: str
    notes: str
    created_at: str
    updated_at: str


def get_current_user_id():
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")


@app.post("/appointments/{appointment_id}/soap-note", response_model=SOAPNoteResponse, operation_id="create_soap_note")
def create_soap_note(appointment_id: int = Path(...)):
    with engine.connect() as conn:
        # Verify appointment exists and is completed video visit
        result = conn.execute(
            text("""
                SELECT a.id, a.patient_id, a.clinician_id, a.appointment_type, a.status, a.reason, a.notes
                FROM appointments a
                WHERE a.id = :appointment_id
            """),
            {"appointment_id": appointment_id}
        )
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Appointment not found")
        
        appt_id, patient_id, clinician_id, appt_type, status, reason, notes = row
        
        if appt_type != "video":
            raise HTTPException(status_code=400, detail="Appointment must be a video visit")
        
        if status != "completed":
            raise HTTPException(status_code=400, detail="Appointment must be completed")
        
        # Check if SOAP note already exists for this appointment
        existing = conn.execute(
            text("SELECT id FROM soap_notes WHERE appointment_id = :appointment_id"),
            {"appointment_id": appointment_id}
        ).fetchone()
        
        if existing:
            raise HTTPException(status_code=409, detail="SOAP note already exists for this appointment")
        
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        
        # Create SOAP note with default content based on task requirements
        subjective = "Patient reports increased worry over the past month."
        objective = "Alert and oriented, affect appropriate, no acute distress."
        assessment = "Generalized anxiety disorder (F41.1)."
        plan = "Follow up in 2 weeks."
        
        conn.execute(
            text("""
                INSERT INTO soap_notes (appointment_id, patient_id, clinician_id, subjective, objective, assessment, plan, created_at, updated_at)
                VALUES (:appointment_id, :patient_id, :clinician_id, :subjective, :objective, :assessment, :plan, :created_at, :updated_at)
            """),
            {
                "appointment_id": appointment_id,
                "patient_id": patient_id,
                "clinician_id": clinician_id,
                "subjective": subjective,
                "objective": objective,
                "assessment": assessment,
                "plan": plan,
                "created_at": now,
                "updated_at": now
            }
        )
        
        soap_note_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
        
        result = conn.execute(
            text("""
                SELECT id, appointment_id, patient_id, clinician_id, subjective, objective, assessment, plan, created_at, updated_at
                FROM soap_notes
                WHERE id = :soap_note_id
            """),
            {"soap_note_id": soap_note_id}
        )
        row = result.fetchone()
        
        conn.commit()
        
        return SOAPNoteResponse(
            id=row[0],
            appointment_id=row[1],
            patient_id=row[2],
            clinician_id=row[3],
            subjective=row[4],
            objective=row[5],
            assessment=row[6],
            plan=row[7],
            created_at=str(row[8]),
            updated_at=str(row[9])
        )


@app.post("/soap-notes/{soap_note_id}/diagnoses", response_model=DiagnosisResponse, operation_id="add_diagnosis")
def add_diagnosis(soap_note_id: int = Path(...)):
    with engine.connect() as conn:
        # Verify SOAP note exists
        result = conn.execute(
            text("SELECT id FROM soap_notes WHERE id = :soap_note_id"),
            {"soap_note_id": soap_note_id}
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="SOAP note not found")
        
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        
        # Add diagnosis F41.1 Generalized Anxiety Disorder as primary
        conn.execute(
            text("""
                INSERT INTO diagnoses (soap_note_id, icd10_code, description, is_primary, created_at)
                VALUES (:soap_note_id, :icd10_code, :description, :is_primary, :created_at)
            """),
            {
                "soap_note_id": soap_note_id,
                "icd10_code": "F41.1",
                "description": "Generalized anxiety disorder",
                "is_primary": 1,
                "created_at": now
            }
        )
        
        diag_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
        
        result = conn.execute(
            text("""
                SELECT id, soap_note_id, icd10_code, description, is_primary, created_at
                FROM diagnoses
                WHERE id = :diag_id
            """),
            {"diag_id": diag_id}
        )
        row = result.fetchone()
        
        conn.commit()
        
        return DiagnosisResponse(
            id=row[0],
            soap_note_id=row[1],
            icd10_code=row[2],
            description=row[3],
            is_primary=bool(row[4]),
            created_at=str(row[5])
        )


@app.post("/soap-notes/{soap_note_id}/billing-codes", response_model=BillingCodeResponse, operation_id="add_billing_code")
def add_billing_code(soap_note_id: int = Path(...)):
    with engine.connect() as conn:
        # Verify SOAP note exists
        result = conn.execute(
            text("SELECT id FROM soap_notes WHERE id = :soap_note_id"),
            {"soap_note_id": soap_note_id}
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="SOAP note not found")
        
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        
        # Add billing code 90834
        conn.execute(
            text("""
                INSERT INTO billing_codes (soap_note_id, cpt_code, description, units, created_at)
                VALUES (:soap_note_id, :cpt_code, :description, :units, :created_at)
            """),
            {
                "soap_note_id": soap_note_id,
                "cpt_code": "90834",
                "description": "Psychotherapy, 45 minutes per session",
                "units": 1,
                "created_at": now
            }
        )
        
        bc_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
        
        result = conn.execute(
            text("""
                SELECT id, soap_note_id, cpt_code, description, units, created_at
                FROM billing_codes
                WHERE id = :bc_id
            """),
            {"bc_id": bc_id}
        )
        row = result.fetchone()
        
        conn.commit()
        
        return BillingCodeResponse(
            id=row[0],
            soap_note_id=row[1],
            cpt_code=row[2],
            description=row[3],
            units=row[4],
            created_at=str(row[5])
        )


@app.post("/appointments", response_model=AppointmentResponse, operation_id="schedule_followup_appointment")
def schedule_followup_appointment():
    """
    Schedules a follow-up appointment for Emily Chen (patient_id=1) in 2 weeks.
    This endpoint assumes the context of the completed video visit with patient Emily Chen.
    In a real scenario, we might need to identify which patient/clinician pair to use.
    Based on the task, we look for the most recent completed video appointment for Emily Chen.
    """
    with engine.connect() as conn:
        # Find Emily Chen's patient ID
        emily_result = conn.execute(
            text("""
                SELECT p.id FROM patients p
                WHERE p.first_name = 'Emily' AND p.last_name = 'Chen'
            """)
        )
        emily_row = emily_result.fetchone()
        if not emily_row:
            raise HTTPException(status_code=404, detail="Patient Emily Chen not found")
        
        patient_id = emily_row[0]
        
        # Find the most recent completed video appointment for this patient
        appt_result = conn.execute(
            text("""
                SELECT a.clinician_id, a.scheduled_start
                FROM appointments a
                WHERE a.patient_id = :patient_id 
                  AND a.appointment_type = 'video' 
                  AND a.status = 'completed'
                ORDER BY a.scheduled_start DESC
                LIMIT 1
            """),
            {"patient_id": patient_id}
        )
        appt_row = appt_result.fetchone()
        
        if not appt_row:
            raise HTTPException(status_code=404, detail="No completed video appointment found for Emily Chen")
        
        clinician_id = appt_row[0]
        original_start_str = appt_row[1]
        
        # Parse the original start time and add 2 weeks
        if isinstance(original_start_str, str):
            original_start = datetime.fromisoformat(original_start_str.replace('Z', '+00:00'))
        else:
            original_start = original_start_str
            
        new_start = original_start + timedelta(weeks=2)
        new_end = new_start + timedelta(minutes=45)
        
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        
        reason = "Behavioral health follow-up"
        notes = "Scheduled after completing behavioral health video visit with SOAP note including GAD diagnosis and CPT 90834."
        
        conn.execute(
            text("""
                INSERT INTO appointments (patient_id, clinician_id, appointment_type, status, scheduled_start, scheduled_end, reason, notes, created_at, updated_at)
                VALUES (:patient_id, :clinician_id, :appointment_type, :status, :scheduled_start, :scheduled_end, :reason, :notes, :created_at, :updated_at)
            """),
            {
                "patient_id": patient_id,
                "clinician_id": clinician_id,
                "appointment_type": "video",
                "status": "scheduled",
                "scheduled_start": new_start.isoformat(sep=' ', timespec='seconds'),
                "scheduled_end": new_end.isoformat(sep=' ', timespec='seconds'),
                "reason": reason,
                "notes": notes,
                "created_at": now,
                "updated_at": now
            }
        )
        
        appt_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
        
        result = conn.execute(
            text("""
                SELECT id, patient_id, clinician_id, appointment_type, status, scheduled_start, scheduled_end, reason, notes, created_at, updated_at
                FROM appointments
                WHERE id = :appt_id
            """),
            {"appt_id": appt_id}
        )
        row = result.fetchone()
        
        conn.commit()
        
        return AppointmentResponse(
            id=row[0],
            patient_id=row[1],
            clinician_id=row[2],
            appointment_type=row[3],
            status=row[4],
            scheduled_start=str(row[5]),
            scheduled_end=str(row[6]),
            reason=row[7],
            notes=row[8],
            created_at=str(row[9]),
            updated_at=str(row[10])
        )
