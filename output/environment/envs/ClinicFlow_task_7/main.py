import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="ClinicFlow Claims API")


class CreateClaimRequest(BaseModel):
    patient_id: int = Field(..., description="Internal ID of the patient")
    encounter_date: str = Field(..., description="Date of the encounter in YYYY-MM-DD format")
    provider_id: int = Field(..., description="Internal ID of the provider")
    cpt_code: str = Field(..., description="CPT code for the procedure performed")
    icd10_code: str = Field(..., description="ICD-10 code for the diagnosis")


class ClaimResponse(BaseModel):
    claim_id: int
    claim_number: str
    status: str


class SubmitClaimResponse(BaseModel):
    claim_id: int
    status: str
    submission_id: int
    submission_status: str


def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()


@app.post("/claims", response_model=ClaimResponse, operation_id="create_cms1500_claim")
def create_cms1500_claim(request: CreateClaimRequest):
    with engine.connect() as conn:
        # Validate patient exists
        patient_row = conn.execute(
            text("SELECT id FROM patients WHERE id = :pid"),
            {"pid": request.patient_id}
        ).fetchone()
        if not patient_row:
            raise HTTPException(status_code=404, detail="Patient not found")

        # Validate provider exists
        provider_row = conn.execute(
            text("SELECT id FROM providers WHERE id = :prid"),
            {"prid": request.provider_id}
        ).fetchone()
        if not provider_row:
            raise HTTPException(status_code=404, detail="Provider not found")

        # Find encounter matching patient_id, provider_id, and encounter_date
        encounter_row = conn.execute(
            text("""
                SELECT id, status FROM encounters 
                WHERE patient_id = :pid AND provider_id = :prid AND encounter_date = :edate
            """),
            {
                "pid": request.patient_id,
                "prid": request.provider_id,
                "edate": request.encounter_date
            }
        ).fetchone()
        if not encounter_row:
            raise HTTPException(status_code=404, detail="Encounter not found")

        encounter_id = encounter_row[0]
        encounter_status = encounter_row[1]

        # Validate CPT code exists
        cpt_row = conn.execute(
            text("SELECT code, description FROM cpt_codes WHERE code = :code"),
            {"code": request.cpt_code}
        ).fetchone()
        if not cpt_row:
            raise HTTPException(status_code=404, detail="CPT code not found")

        # Validate ICD-10 code exists
        icd10_row = conn.execute(
            text("SELECT code, description FROM icd10_codes WHERE code = :code"),
            {"code": request.icd10_code}
        ).fetchone()
        if not icd10_row:
            raise HTTPException(status_code=404, detail="ICD-10 code not found")

        # Get primary insurance for the patient
        insurance_row = conn.execute(
            text("""
                SELECT pi.id, pi.payer_id 
                FROM patient_insurance pi 
                WHERE pi.patient_id = :pid AND pi.is_primary = 1
            """),
            {"pid": request.patient_id}
        ).fetchone()
        if not insurance_row:
            raise HTTPException(status_code=404, detail="No primary insurance found for patient")

        patient_insurance_id = insurance_row[0]
        payer_id = insurance_row[1]

        # Check if a claim already exists for this encounter
        existing_claim = conn.execute(
            text("SELECT id FROM claims WHERE encounter_id = :eid"),
            {"eid": encounter_id}
        ).fetchone()
        if existing_claim:
            raise HTTPException(status_code=409, detail="A claim already exists for this encounter")

        # Determine charge amount from encounter_procedures or default to 0
        proc_row = conn.execute(
            text("""
                SELECT charge_amount FROM encounter_procedures 
                WHERE encounter_id = :eid AND cpt_code = :cpt
            """),
            {"eid": encounter_id, "cpt": request.cpt_code}
        ).fetchone()
        
        total_charge = proc_row[0] if proc_row and proc_row[0] is not None else 0.0

        # Generate claim number
        now = datetime.now(timezone.utc)
        year = now.year
        
        # Count existing claims for this year to generate sequence
        count_row = conn.execute(
            text("SELECT COUNT(*) FROM claims WHERE claim_number LIKE :pattern"),
            {"pattern": f"CLM-{year}-%"}
        ).fetchone()
        seq_num = (count_row[0] + 1) if count_row else 1
        claim_number = f"CLM-{year}-{seq_num:04d}"

        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        # Insert claim
        result = conn.execute(
            text("""
                INSERT INTO claims (claim_number, patient_id, encounter_id, provider_id, payer_id, 
                                    patient_insurance_id, claim_type, total_charge, status, created_at, updated_at)
                VALUES (:cn, :pid, :eid, :prid, :payid, :pinsid, 'CMS-1500', :tc, 'draft', :ts, :ts)
            """),
            {
                "cn": claim_number,
                "pid": request.patient_id,
                "eid": encounter_id,
                "prid": request.provider_id,
                "payid": payer_id,
                "pinsid": patient_insurance_id,
                "tc": total_charge,
                "ts": timestamp
            }
        )
        claim_id = result.lastrowid

        # Insert claim_diagnoses
        conn.execute(
            text("""
                INSERT INTO claim_diagnoses (claim_id, icd10_code, diagnosis_order, created_at, updated_at)
                VALUES (:cid, :icd, 1, :ts, :ts)
            """),
            {"cid": claim_id, "icd": request.icd10_code, "ts": timestamp}
        )

        # Insert claim_procedures
        conn.execute(
            text("""
                INSERT INTO claim_procedures (claim_id, cpt_code, units, charge_amount, created_at, updated_at)
                VALUES (:cid, :cpt, 1, :ca, :ts, :ts)
            """),
            {"cid": claim_id, "cpt": request.cpt_code, "ca": total_charge, "ts": timestamp}
        )

        conn.commit()

        return ClaimResponse(
            claim_id=claim_id,
            claim_number=claim_number,
            status="draft"
        )


@app.post("/claims/{claim_id}/submit", response_model=SubmitClaimResponse, operation_id="submit_claim")
def submit_claim(claim_id: int = Path(..., description="Internal ID of the claim to submit")):
    with engine.connect() as conn:
        # Fetch claim details
        claim_row = conn.execute(
            text("""
                SELECT c.id, c.status, c.payer_id, c.total_charge
                FROM claims c
                WHERE c.id = :cid
            """),
            {"cid": claim_id}
        ).fetchone()
        
        if not claim_row:
            raise HTTPException(status_code=404, detail="Claim not found")

        current_status = claim_row[1]
        payer_id = claim_row[2]

        if current_status != "draft":
            raise HTTPException(status_code=400, detail=f"Cannot submit claim with status '{current_status}'. Only draft claims can be submitted.")

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        # Update claim status to submitted
        conn.execute(
            text("""
                UPDATE claims 
                SET status = 'submitted', submitted_at = :ts, updated_at = :ts
                WHERE id = :cid
            """),
            {"cid": claim_id, "ts": timestamp}
        )

        # Create claim_submission record
        sub_result = conn.execute(
            text("""
                INSERT INTO claim_submissions (claim_id, payer_id, submission_method, submission_status, submitted_at, created_at, updated_at)
                VALUES (:cid, :pid, 'electronic', 'sent', :ts, :ts, :ts)
            """),
            {
                "cid": claim_id,
                "pid": payer_id,
                "ts": timestamp
            }
        )
        submission_id = sub_result.lastrowid

        conn.commit()

        return SubmitClaimResponse(
            claim_id=claim_id,
            status="submitted",
            submission_id=submission_id,
            submission_status="sent"
        )
