import os
from datetime import datetime

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CreateEncounterRequest(BaseModel):
    provider_id: int


class SoapNoteRequest(BaseModel):
    subjective: str
    objective: str
    assessment: str
    plan: str


class DiagnosisRequest(BaseModel):
    icd10_code: str


class PrescriptionRequest(BaseModel):
    medication_name: str
    strength: str
    dosage: str
    frequency: str
    pharmacy_name: str


@app.post("/patients/{mrn}/encounters", operation_id="create_followup_encounter")
def create_followup_encounter(
    mrn: str = Path(..., description="Medical Record Number of the patient"),
    body: CreateEncounterRequest = ...,
):
    with engine.begin() as conn:
        # Find patient by MRN
        result = conn.execute(text("SELECT id FROM patients WHERE mrn = :mrn"), {"mrn": mrn})
        patient_row = result.fetchone()
        if not patient_row:
            raise HTTPException(status_code=404, detail=f"Patient with MRN {mrn} not found")
        patient_id = patient_row[0]

        # Verify provider exists
        result = conn.execute(text("SELECT id FROM users WHERE id = :pid AND role = 'provider'"), {"pid": body.provider_id})
        provider_row = result.fetchone()
        if not provider_row:
            raise HTTPException(status_code=404, detail=f"Provider with ID {body.provider_id} not found")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text("""
                INSERT INTO encounters (patient_id, provider_id, encounter_type, encounter_date, status, created_at, updated_at)
                VALUES (:patient_id, :provider_id, :encounter_type, :encounter_date, :status, :created_at, :updated_at)
            """),
            {
                "patient_id": patient_id,
                "provider_id": body.provider_id,
                "encounter_type": "follow-up visit",
                "encounter_date": now,
                "status": "open",
                "created_at": now,
                "updated_at": now,
            },
        )
        encounter_id = result.lastrowid

    return {"encounter_id": encounter_id}


@app.post("/encounters/{encounter_id}/soap_note", operation_id="document_soap_note")
def document_soap_note(
    encounter_id: int = Path(..., description="ID of the encounter to which the SOAP note belongs"),
    body: SoapNoteRequest = ...,
):
    with engine.begin() as conn:
        # Verify encounter exists
        result = conn.execute(text("SELECT id FROM encounters WHERE id = :eid"), {"eid": encounter_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"Encounter with ID {encounter_id} not found")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text("""
                INSERT INTO soap_notes (encounter_id, subjective, objective, assessment, plan, created_at, updated_at)
                VALUES (:encounter_id, :subjective, :objective, :assessment, :plan, :created_at, :updated_at)
            """),
            {
                "encounter_id": encounter_id,
                "subjective": body.subjective,
                "objective": body.objective,
                "assessment": body.assessment,
                "plan": body.plan,
                "created_at": now,
                "updated_at": now,
            },
        )
        note_id = result.lastrowid

    return {"note_id": note_id}


@app.post("/encounters/{encounter_id}/diagnoses", operation_id="assign_icd10_diagnosis")
def assign_icd10_diagnosis(
    encounter_id: int = Path(..., description="ID of the encounter to which the diagnosis is assigned"),
    body: DiagnosisRequest = ...,
):
    with engine.begin() as conn:
        # Verify encounter exists
        result = conn.execute(text("SELECT id FROM encounters WHERE id = :eid"), {"eid": encounter_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"Encounter with ID {encounter_id} not found")

        # Verify ICD-10 code exists
        result = conn.execute(text("SELECT code FROM icd10_codes WHERE code = :code"), {"code": body.icd10_code})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"ICD-10 code {body.icd10_code} not found")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text("""
                INSERT INTO encounter_diagnoses (encounter_id, icd10_code, diagnosis_type, created_at, updated_at)
                VALUES (:encounter_id, :icd10_code, :diagnosis_type, :created_at, :updated_at)
            """),
            {
                "encounter_id": encounter_id,
                "icd10_code": body.icd10_code,
                "diagnosis_type": "primary",
                "created_at": now,
                "updated_at": now,
            },
        )
        diagnosis_id = result.lastrowid

    return {"diagnosis_id": diagnosis_id}


@app.post("/encounters/{encounter_id}/prescriptions", operation_id="eprescribe_medication")
def eprescribe_medication(
    encounter_id: int = Path(..., description="ID of the encounter for which the prescription is being made"),
    body: PrescriptionRequest = ...,
):
    with engine.begin() as conn:
        # Verify encounter exists
        result = conn.execute(text("SELECT id FROM encounters WHERE id = :eid"), {"eid": encounter_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"Encounter with ID {encounter_id} not found")

        # Find medication by name and strength
        result = conn.execute(
            text("SELECT id FROM medications WHERE name = :name AND strength = :strength"),
            {"name": body.medication_name, "strength": body.strength},
        )
        med_row = result.fetchone()
        if not med_row:
            raise HTTPException(
                status_code=404,
                detail=f"Medication '{body.medication_name}' with strength '{body.strength}' not found",
            )
        medication_id = med_row[0]

        # Find pharmacy by name
        result = conn.execute(text("SELECT id FROM pharmacies WHERE name = :name"), {"name": body.pharmacy_name})
        pharm_row = result.fetchone()
        if not pharm_row:
            raise HTTPException(status_code=404, detail=f"Pharmacy '{body.pharmacy_name}' not found")
        pharmacy_id = pharm_row[0]

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text("""
                INSERT INTO prescriptions (encounter_id, medication_id, pharmacy_id, dosage, frequency, status, prescribed_at, created_at, updated_at)
                VALUES (:encounter_id, :medication_id, :pharmacy_id, :dosage, :frequency, :status, :prescribed_at, :created_at, :updated_at)
            """),
            {
                "encounter_id": encounter_id,
                "medication_id": medication_id,
                "pharmacy_id": pharmacy_id,
                "dosage": body.dosage,
                "frequency": body.frequency,
                "status": "pending",
                "prescribed_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
        prescription_id = result.lastrowid

    return {"prescription_id": prescription_id}
