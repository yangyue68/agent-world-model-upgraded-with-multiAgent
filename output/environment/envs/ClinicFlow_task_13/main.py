import os
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from typing import Optional, List

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models for Responses ---

class EncounterResponse(BaseModel):
    id: int
    patient_id: int
    provider_id: int
    encounter_date: str
    encounter_type: str
    status: str


class SoapNoteResponse(BaseModel):
    id: int
    encounter_id: int
    subjective: Optional[str] = None
    objective: Optional[str] = None
    assessment: Optional[str] = None
    plan: Optional[str] = None


class DiagnosisResponse(BaseModel):
    code: str
    description: str


class FindEncounterResponse(BaseModel):
    encounter: EncounterResponse
    soap_note: SoapNoteResponse
    diagnoses: List[DiagnosisResponse]


class AddendumCreateRequest(BaseModel):
    soap_note_id: int
    author_id: int
    content: str


class AddendumResponse(BaseModel):
    id: int
    soap_note_id: int
    author_id: int
    content: str
    created_at: str


class EncounterDiagnosisCreateRequest(BaseModel):
    encounter_id: int
    icd10_code: str


class EncounterDiagnosisResponse(BaseModel):
    id: int
    encounter_id: int
    icd10_code: str
    diagnosis_order: int
    created_at: str


class LabOrderCreateRequest(BaseModel):
    encounter_id: int
    lab_test_id: int
    ordered_by: int


class LabOrderResponse(BaseModel):
    id: int
    encounter_id: int
    lab_test_id: int
    ordered_by: int
    status: str
    ordered_at: str
    created_at: str
    updated_at: str


# --- Endpoints ---

@app.get("/encounters", response_model=FindEncounterResponse, operation_id="find_encounter_for_patient_yesterday")
def find_encounter_for_patient_yesterday(
    mrn: str = Query(..., description="Medical Record Number of the patient"),
    encounter_date: str = Query(..., description="Date of the encounter in YYYY-MM-DD format")
):
    """
    Retrieve the encounter record for patient Jane Doe (MRN 67890) that occurred yesterday,
    including associated SOAP note and diagnoses.
    Note: The spec says 'yesterday', but provides an explicit date param. We use the provided date.
    """
    
    # Validate date format roughly
    try:
        datetime.strptime(encounter_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

    with engine.connect() as conn:
        # 1. Get Patient ID from MRN
        stmt_patient = text("""
            SELECT id FROM patients WHERE mrn = :mrn
        """)
        res_patient = conn.execute(stmt_patient, {"mrn": mrn})
        row_patient = res_patient.fetchone()
        
        if not row_patient:
            raise HTTPException(status_code=404, detail=f"Patient with MRN {mrn} not found.")
        
        patient_id = row_patient[0]

        # 2. Get Encounter for this patient on this date
        stmt_encounter = text("""
            SELECT id, patient_id, provider_id, encounter_date, encounter_type, status 
            FROM encounters 
            WHERE patient_id = :patient_id AND encounter_date = :encounter_date
        """)
        res_encounter = conn.execute(stmt_encounter, {"patient_id": patient_id, "encounter_date": encounter_date})
        row_encounter = res_encounter.fetchone()

        if not row_encounter:
            raise HTTPException(status_code=404, detail=f"No encounter found for patient {mrn} on {encounter_date}.")

        encounter_data = {
            "id": row_encounter[0],
            "patient_id": row_encounter[1],
            "provider_id": row_encounter[2],
            "encounter_date": str(row_encounter[3]),
            "encounter_type": row_encounter[4],
            "status": row_encounter[5]
        }
        encounter_id = encounter_data["id"]

        # 3. Get SOAP Note for this encounter
        stmt_soap = text("""
            SELECT id, encounter_id, subjective, objective, assessment, plan 
            FROM soap_notes 
            WHERE encounter_id = :encounter_id
        """)
        res_soap = conn.execute(stmt_soap, {"encounter_id": encounter_id})
        row_soap = res_soap.fetchone()

        if not row_soap:
            # Spec implies there should be one, but handle gracefully or error? 
            # Given the task context, we assume it exists. If not, maybe return nulls or error.
            # Let's raise 404 as per typical REST behavior for missing sub-resource if required.
            raise HTTPException(status_code=404, detail="SOAP note not found for this encounter.")

        soap_data = {
            "id": row_soap[0],
            "encounter_id": row_soap[1],
            "subjective": row_soap[2],
            "objective": row_soap[3],
            "assessment": row_soap[4],
            "plan": row_soap[5]
        }

        # 4. Get Diagnoses for this encounter
        stmt_diag = text("""
            SELECT ed.icd10_code, i.description
            FROM encounter_diagnoses ed
            JOIN icd10_codes i ON ed.icd10_code = i.code
            WHERE ed.encounter_id = :encounter_id
            ORDER BY ed.diagnosis_order ASC
        """)
        res_diag = conn.execute(stmt_diag, {"encounter_id": encounter_id})
        rows_diag = res_diag.fetchall()

        diagnoses_list = [
            {"code": r[0], "description": r[1]} for r in rows_diag
        ]

        return FindEncounterResponse(
            encounter=EncounterResponse(**encounter_data),
            soap_note=SoapNoteResponse(**soap_data),
            diagnoses=[DiagnosisResponse(**d) for d in diagnoses_list]
        )


@app.post("/addenda", response_model=AddendumResponse, operation_id="add_addendum_to_soap_note")
def add_addendum_to_soap_note(request: AddendumCreateRequest):
    """
    Add an addendum to the SOAP note.
    """
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated.")
    
    # The spec asks for author_id in body, but system instructions say read env var for actor columns.
    # However, the API spec explicitly defines `author_id` as a required body parameter.
    # Usually, if the spec requires it, we might trust it OR override it with security context.
    # Instruction: "When a write must record an actor column ... read that environment variable at request time; never hardcode...".
    # It also says: "Do not add mandatory auth/body/header fields absent from the spec."
    # Here `author_id` IS in the spec. But typically, server-side logic should enforce who the user is.
    # Let's look at the instruction again: "read that environment variable at request time".
    # This usually implies ignoring client-provided IDs for audit fields if possible, BUT the schema has `author_id`.
    # If I ignore the body `author_id` and use env, does it break the contract?
    # The prompt says: "Use the exact method, path, operation_id, parameter names and response semantics in the API specification."
    # And: "never add an auth header/body field absent from the spec." -> Author_id is present.
    # Conflict resolution: In many secure systems, you validate that body.author_id == env.user_id, or just use env.
    # Given "retrieve simulated logged-in actor... when a write must record an actor column... read that env var",
    # I will prioritize the Env Var for the actual DB insert to ensure integrity, assuming the client sends it correctly or we overwrite it.
    # Actually, looking at similar tasks, often the `author_id` in body is ignored in favor of `AWM_CURRENT_USER_ID`.
    # Let's use `int(current_user_id_str)` for the database insertion.
    
    try:
        author_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID in environment.")

    # Verify SOAP note exists
    with engine.begin() as conn:
        stmt_check = text("SELECT id FROM soap_notes WHERE id = :id")
        res = conn.execute(stmt_check, {"id": request.soap_note_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="SOAP note not found.")

        # Insert Addendum
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        stmt_insert = text("""
            INSERT INTO addenda (soap_note_id, author_id, content, created_at)
            VALUES (:soap_note_id, :author_id, :content, :created_at)
        """)
        result = conn.execute(stmt_insert, {
            "soap_note_id": request.soap_note_id,
            "author_id": author_id,
            "content": request.content,
            "created_at": now
        })
        
        new_id = result.lastrowid
        
        # Fetch back to get consistent formatting/timestamps if needed, though we constructed them.
        # SQLite CURRENT_TIMESTAMP might differ slightly from python utcnow depending on config.
        # Since we passed explicit string, let's fetch to be safe and accurate to DB state.
        stmt_fetch = text("""
            SELECT id, soap_note_id, author_id, content, created_at 
            FROM addenda 
            WHERE id = :id
        """)
        res_fetch = conn.execute(stmt_fetch, {"id": new_id})
        row = res_fetch.fetchone()
        
        return AddendumResponse(
            id=row[0],
            soap_note_id=row[1],
            author_id=row[2],
            content=row[3],
            created_at=str(row[4])
        )


@app.post("/encounter_diagnoses", response_model=EncounterDiagnosisResponse, operation_id="assign_icd10_code_to_encounter")
def assign_icd10_code_to_encounter(request: EncounterDiagnosisCreateRequest):
    """
    Assign ICD-10 code to the encounter.
    """
    with engine.begin() as conn:
        # Check if encounter exists
        stmt_enc = text("SELECT id FROM encounters WHERE id = :id")
        if not conn.execute(stmt_enc, {"id": request.encounter_id}).fetchone():
            raise HTTPException(status_code=404, detail="Encounter not found.")

        # Check if ICD10 code exists
        stmt_icd = text("SELECT code FROM icd10_codes WHERE code = :code")
        if not conn.execute(stmt_icd, {"code": request.icd10_code}).fetchone():
            raise HTTPException(status_code=404, detail="ICD-10 code not found.")

        # Determine next diagnosis_order
        stmt_max_order = text("""
            SELECT COALESCE(MAX(diagnosis_order), 0) + 1 
            FROM encounter_diagnoses 
            WHERE encounter_id = :encounter_id
        """)
        max_order_res = conn.execute(stmt_max_order, {"encounter_id": request.encounter_id})
        next_order = max_order_res.scalar()

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
        stmt_insert = text("""
            INSERT INTO encounter_diagnoses (encounter_id, icd10_code, diagnosis_order, created_at)
            VALUES (:encounter_id, :icd10_code, :diagnosis_order, :created_at)
        """)
        try:
            result = conn.execute(stmt_insert, {
                "encounter_id": request.encounter_id,
                "icd10_code": request.icd10_code,
                "diagnosis_order": next_order,
                "created_at": now
            })
            new_id = result.lastrowid
        except Exception as e:
            # Handle unique constraint violation if necessary, though logic above tries to prevent duplicates via order?
            # Schema has UNIQUE(encounter_id, icd10_code). So duplicate codes are blocked regardless of order.
            if "UNIQUE constraint failed" in str(e):
                 raise HTTPException(status_code=409, detail="This diagnosis code is already assigned to this encounter.")
            raise e

        stmt_fetch = text("""
            SELECT id, encounter_id, icd10_code, diagnosis_order, created_at 
            FROM encounter_diagnoses 
            WHERE id = :id
        """)
        row = conn.execute(stmt_fetch, {"id": new_id}).fetchone()
        
        return EncounterDiagnosisResponse(
            id=row[0],
            encounter_id=row[1],
            icd10_code=row[2],
            diagnosis_order=row[3],
            created_at=str(row[4])
        )


@app.post("/lab_orders", response_model=LabOrderResponse, operation_id="order_lab_test")
def order_lab_test(request: LabOrderCreateRequest):
    """
    Order a lab test for influenza PCR for the given encounter.
    """
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated.")
    
    try:
        ordered_by_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID in environment.")

    with engine.begin() as conn:
        # Check encounter exists
        stmt_enc = text("SELECT id FROM encounters WHERE id = :id")
        if not conn.execute(stmt_enc, {"id": request.encounter_id}).fetchone():
            raise HTTPException(status_code=404, detail="Encounter not found.")

        # Check lab test exists
        stmt_test = text("SELECT id FROM lab_tests WHERE id = :id")
        if not conn.execute(stmt_test, {"id": request.lab_test_id}).fetchone():
            raise HTTPException(status_code=404, detail="Lab test not found.")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        status_default = "ordered"

        stmt_insert = text("""
            INSERT INTO lab_orders (encounter_id, lab_test_id, ordered_by, status, ordered_at, created_at, updated_at)
            VALUES (:encounter_id, :lab_test_id, :ordered_by, :status, :ordered_at, :created_at, :updated_at)
        """)
        
        result = conn.execute(stmt_insert, {
            "encounter_id": request.encounter_id,
            "lab_test_id": request.lab_test_id,
            "ordered_by": ordered_by_id,
            "status": status_default,
            "ordered_at": now,
            "created_at": now,
            "updated_at": now
        })
        
        new_id = result.lastrowid

        stmt_fetch = text("""
            SELECT id, encounter_id, lab_test_id, ordered_by, status, ordered_at, created_at, updated_at 
            FROM lab_orders 
            WHERE id = :id
        """)
        row = conn.execute(stmt_fetch, {"id": new_id}).fetchone()
        
        return LabOrderResponse(
            id=row[0],
            encounter_id=row[1],
            lab_test_id=row[2],
            ordered_by=row[3],
            status=row[4],
            ordered_at=str(row[5]),
            created_at=str(row[6]),
            updated_at=str(row[7])
        )
