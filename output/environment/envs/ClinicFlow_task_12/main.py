import os
from datetime import date, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CreateEncounterRequest(BaseModel):
    patient_mrn: str
    provider_npi: str
    encounter_date: str


class SoapNoteRequest(BaseModel):
    encounter_id: int
    subjective: str
    objective: str
    assessment: str
    plan: str


class DiagnosisRequest(BaseModel):
    encounter_id: int
    icd10_code: str


class OrderRequest(BaseModel):
    encounter_id: int
    order_type: str
    description: str


class PrescriptionRequest(BaseModel):
    encounter_id: int
    pharmacy_name: str
    medication_name: str
    dosage: str
    frequency: str | None = None
    route: str | None = None
    quantity: str | None = None


@app.post("/encounters", operation_id="create_encounter")
def create_encounter(req: CreateEncounterRequest):
    with engine.begin() as conn:
        # Resolve patient by MRN
        row = conn.execute(
            text("SELECT id FROM patients WHERE mrn = :mrn"),
            {"mrn": req.patient_mrn},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Patient not found")
        patient_id = row[0]

        # Resolve provider by NPI
        row = conn.execute(
            text("SELECT id FROM providers WHERE npi = :npi"),
            {"npi": req.provider_npi},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Provider not found")
        provider_id = row[0]

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text(
                """INSERT INTO encounters (patient_id, provider_id, encounter_date, encounter_type, status, created_at, updated_at)
                   VALUES (:pid, :prov_id, :edate, 'office_visit', 'open', :now, :now)"""
            ),
            {
                "pid": patient_id,
                "prov_id": provider_id,
                "edate": req.encounter_date,
                "now": now,
            },
        )
        encounter_id = result.lastrowid

    return {"encounter_id": encounter_id}


@app.post("/soap_notes", operation_id="create_soap_note")
def create_soap_note(req: SoapNoteRequest):
    with engine.begin() as conn:
        # Verify encounter exists
        row = conn.execute(
            text("SELECT id FROM encounters WHERE id = :eid"),
            {"eid": req.encounter_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Encounter not found")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text(
                """INSERT INTO soap_notes (encounter_id, subjective, objective, assessment, plan, created_at, updated_at)
                   VALUES (:eid, :subj, :obj, :assess, :plan, :now, :now)"""
            ),
            {
                "eid": req.encounter_id,
                "subj": req.subjective,
                "obj": req.objective,
                "assess": req.assessment,
                "plan": req.plan,
                "now": now,
            },
        )
        soap_note_id = result.lastrowid

    return {"soap_note_id": soap_note_id}


@app.post("/diagnoses", operation_id="assign_diagnosis")
def assign_diagnosis(req: DiagnosisRequest):
    with engine.begin() as conn:
        # Verify encounter exists
        row = conn.execute(
            text("SELECT id FROM encounters WHERE id = :eid"),
            {"eid": req.encounter_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Encounter not found")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text(
                """INSERT INTO diagnoses (encounter_id, icd10_code, created_at, updated_at)
                   VALUES (:eid, :code, :now, :now)"""
            ),
            {
                "eid": req.encounter_id,
                "code": req.icd10_code,
                "now": now,
            },
        )
        diagnosis_id = result.lastrowid

    return {"diagnosis_id": diagnosis_id}


@app.post("/orders", operation_id="order_chest_xray")
def order_chest_xray(req: OrderRequest):
    with engine.begin() as conn:
        # Verify encounter exists
        row = conn.execute(
            text("SELECT id FROM encounters WHERE id = :eid"),
            {"eid": req.encounter_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Encounter not found")

        # Validate order_type against CHECK constraint values
        valid_order_types = ('lab', 'imaging', 'procedure', 'referral', 'other')
        if req.order_type not in valid_order_types:
            raise HTTPException(status_code=422, detail=f"Invalid order_type. Must be one of {valid_order_types}")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text(
                """INSERT INTO orders (encounter_id, order_type, description, status, ordered_at, created_at, updated_at)
                   VALUES (:eid, :otype, :desc, 'ordered', :now, :now, :now)"""
            ),
            {
                "eid": req.encounter_id,
                "otype": req.order_type,
                "desc": req.description,
                "now": now,
            },
        )
        order_id = result.lastrowid

    return {"order_id": order_id}


@app.post("/prescriptions", operation_id="e_prescribe_azithromycin")
def e_prescribe_azithromycin(req: PrescriptionRequest):
    with engine.begin() as conn:
        # Verify encounter exists
        row = conn.execute(
            text("SELECT id FROM encounters WHERE id = :eid"),
            {"eid": req.encounter_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Encounter not found")

        # Resolve pharmacy by name
        row = conn.execute(
            text("SELECT id FROM pharmacies WHERE name = :pname"),
            {"pname": req.pharmacy_name},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Pharmacy not found")
        pharmacy_id = row[0]

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text(
                """INSERT INTO prescriptions (encounter_id, pharmacy_id, medication_name, dosage, frequency, route, quantity, status, prescribed_at, created_at, updated_at)
                   VALUES (:eid, :phid, :med, :dose, :freq, :route, :qty, 'prescribed', :now, :now, :now)"""
            ),
            {
                "eid": req.encounter_id,
                "phid": pharmacy_id,
                "med": req.medication_name,
                "dose": req.dosage,
                "freq": req.frequency,
                "route": req.route,
                "qty": req.quantity,
                "now": now,
            },
        )
        prescription_id = result.lastrowid

    return {"prescription_id": prescription_id}
