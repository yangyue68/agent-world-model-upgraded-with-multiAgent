import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PatientLookupResponse(BaseModel):
    id: int


class ProviderLookupResponse(BaseModel):
    id: int


class VisitLookupResponse(BaseModel):
    id: int


class CarePlanUpdateResponse(BaseModel):
    care_plan_id: int
    prescription_id: int


class SoapNoteCreateResponse(BaseModel):
    soap_note_id: int
    diagnosis_id: int
    billing_code_id: int


@app.get("/patients/lookup", response_model=PatientLookupResponse, operation_id="lookup_patient_by_name")
def lookup_patient_by_name(
    first_name: str = Query(...),
    last_name: str = Query(...)
):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM patients WHERE first_name = :fn AND last_name = :ln"),
            {"fn": first_name, "ln": last_name}
        ).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    return PatientLookupResponse(id=result[0])


@app.get("/providers/lookup", response_model=ProviderLookupResponse, operation_id="lookup_provider_by_name")
def lookup_provider_by_name(
    first_name: str = Query(...),
    last_name: str = Query(...)
):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM providers WHERE first_name = :fn AND last_name = :ln"),
            {"fn": first_name, "ln": last_name}
        ).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Provider not found")
    
    return ProviderLookupResponse(id=result[0])


@app.get("/visits/lookup", response_model=VisitLookupResponse, operation_id="lookup_visit_by_patient_provider_and_type")
def lookup_visit_by_patient_provider_and_type(
    patient_id: int = Query(...),
    provider_id: int = Query(...),
    visit_type: str = Query(...)
):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id FROM visits 
                WHERE patient_id = :pid AND provider_id = :prid AND visit_type = :vt
            """),
            {"pid": patient_id, "prid": provider_id, "vt": visit_type}
        ).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Visit not found")
    
    return VisitLookupResponse(id=result[0])


@app.post("/visits/{visit_id}/care_plan", response_model=CarePlanUpdateResponse, operation_id="update_care_plan_with_prescription")
def update_care_plan_with_prescription(visit_id: int):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.begin() as conn:
        # Get visit details to find patient and provider
        visit_info = conn.execute(
            text("SELECT patient_id, provider_id FROM visits WHERE id = :vid"),
            {"vid": visit_id}
        ).fetchone()
        
        if not visit_info:
            raise HTTPException(status_code=404, detail="Visit not found")
            
        patient_id, provider_id = visit_info
        
        # Find existing active care plan for this patient and provider linked to this visit or just general chronic care
        # The spec says "update his care plan". Looking at data, there might be an existing one.
        # Let's look for a care plan associated with the visit first, or the most recent active one for the patient/provider pair.
        # In the initial snapshot, care_plan 1 is linked to visit 1 (patient 1, provider 1).
        # If we are updating *the* care plan for this specific video visit context, we should probably link it to this visit if not already present, 
        # or use the existing one if it matches patient/provider.
        
        # Strategy: Check if a care plan exists for this visit_id. If so, use it.
        # If not, check if an active care plan exists for this patient_id and provider_id. If so, maybe update its visit_id? 
        # Or create a new one? The endpoint is "update ... care plan". Usually implies modifying existing structure or adding items.
        # Given the simplicity, let's assume we need to ensure a care plan exists for this interaction.
        # However, looking at the schema, `care_plans` has `visit_id`.
        # Let's try to find a care plan where visit_id == :vid.
        
        cp_row = conn.execute(
            text("SELECT id FROM care_plans WHERE visit_id = :vid"),
            {"vid": visit_id}
        ).fetchone()
        
        if cp_row:
            care_plan_id = cp_row[0]
        else:
            # If no care plan specifically for this visit, does one exist for the patient/provider generally?
            # Task says "update HIS care plan". It likely refers to the ongoing management plan.
            # Let's look for an active care plan for this patient and provider.
            cp_general = conn.execute(
                text("""
                    SELECT id FROM care_plans 
                    WHERE patient_id = :pid AND provider_id = :prid AND status = 'active'
                    ORDER BY created_at DESC LIMIT 1
                """),
                {"pid": patient_id, "prid": provider_id}
            ).fetchone()
            
            if cp_general:
                care_plan_id = cp_general[0]
                # Optionally update the visit_id association if needed, but usually prescriptions link to care_plan_id.
                # We will proceed using this care_plan_id.
            else:
                # Create a new care plan if none exists
                res_cp = conn.execute(
                    text("""
                        INSERT INTO care_plans (patient_id, provider_id, visit_id, title, description, status)
                        VALUES (:pid, :prid, :vid, :title, :desc, 'active')
                    """),
                    {
                        "pid": patient_id,
                        "prid": provider_id,
                        "vid": visit_id,
                        "title": "Chronic Care Management Plan",
                        "desc": "Updated during video visit"
                    }
                )
                care_plan_id = res_cp.lastrowid

        # Insert Prescription
        # Medication: Lisinopril, Dosage: 20mg, Frequency: once daily
        res_rx = conn.execute(
            text("""
                INSERT INTO prescriptions (care_plan_id, patient_id, provider_id, medication_name, dosage, frequency, route, status)
                VALUES (:cpid, :pid, :prid, :med, :dose, :freq, :route, 'active')
            """),
            {
                "cpid": care_plan_id,
                "pid": patient_id,
                "prid": provider_id,
                "med": "Lisinopril",
                "dose": "20mg",
                "freq": "once daily",
                "route": "oral"
            }
        )
        prescription_id = res_rx.lastrowid
        
        return CarePlanUpdateResponse(care_plan_id=care_plan_id, prescription_id=prescription_id)


@app.post("/visits/{visit_id}/soap_note", response_model=SoapNoteCreateResponse, operation_id="create_soap_note_with_diagnosis_and_billing")
def create_soap_note_with_diagnosis_and_billing(visit_id: int):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.begin() as conn:
        # Get visit details
        visit_info = conn.execute(
            text("SELECT patient_id, provider_id FROM visits WHERE id = :vid"),
            {"vid": visit_id}
        ).fetchone()
        
        if not visit_info:
            raise HTTPException(status_code=404, detail="Visit not found")
            
        patient_id, provider_id = visit_info
        
        # Insert SOAP Note
        # Subjective/Objective/Assessment/Plan can be generic or derived from task context.
        # Task mentions diagnosis I10 and billing 99214.
        subj = "Patient reports blood pressure issues."
        obj = "BP elevated."
        assess = "Essential hypertension."
        plan = "Start Lisinopril 20mg daily."
        
        res_sn = conn.execute(
            text("""
                INSERT INTO soap_notes (visit_id, patient_id, provider_id, subjective, objective, assessment, plan)
                VALUES (:vid, :pid, :prid, :subj, :obj, :assess, :plan)
            """),
            {
                "vid": visit_id,
                "pid": patient_id,
                "prid": provider_id,
                "subj": subj,
                "obj": obj,
                "assess": assess,
                "plan": plan
            }
        )
        soap_note_id = res_sn.lastrowid
        
        # Insert Diagnosis
        res_diag = conn.execute(
            text("""
                INSERT INTO diagnoses (soap_note_id, code, description)
                VALUES (:snid, :code, :desc)
            """),
            {
                "snid": soap_note_id,
                "code": "I10",
                "desc": "Essential hypertension"
            }
        )
        diagnosis_id = res_diag.lastrowid
        
        # Insert Billing Code
        res_bill = conn.execute(
            text("""
                INSERT INTO billing_codes (soap_note_id, code, description)
                VALUES (:snid, :code, :desc)
            """),
            {
                "snid": soap_note_id,
                "code": "99214",
                "desc": "Office/outpatient visit, established patient, moderate complexity"
            }
        )
        billing_code_id = res_bill.lastrowid
        
        return SoapNoteCreateResponse(
            soap_note_id=soap_note_id,
            diagnosis_id=diagnosis_id,
            billing_code_id=billing_code_id
        )
