import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ProcedureItem(BaseModel):
    procedure_code: str
    description: Optional[str] = None
    billed_amount: float
    tooth_number: Optional[str] = None
    surface: Optional[str] = None


class ClaimRequest(BaseModel):
    claim_number: str
    patient_id: int
    policy_id: int
    payer_id: int
    total_billed_amount: float
    procedures: List[ProcedureItem]
    notes: Optional[str] = None


@app.post("/claims", operation_id="submit_insurance_claim")
def submit_insurance_claim(request: ClaimRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        submitted_by_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        # Validate Patient exists
        res = conn.execute(text("SELECT id FROM patients WHERE id = :pid"), {"pid": request.patient_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Patient not found")

        # Validate Policy exists and belongs to patient/payer consistency check (optional but good practice)
        res = conn.execute(
            text("SELECT id, patient_id, payer_id FROM patient_insurance_policies WHERE id = :pol_id"),
            {"pol_id": request.policy_id}
        )
        policy_row = res.fetchone()
        if not policy_row:
            raise HTTPException(status_code=404, detail="Insurance policy not found")
        
        if policy_row.patient_id != request.patient_id:
             raise HTTPException(status_code=400, detail="Policy does not belong to the specified patient")
        
        if policy_row.payer_id != request.payer_id:
             raise HTTPException(status_code=400, detail="Payer ID mismatch with policy")

        # Validate Payer exists
        res = conn.execute(text("SELECT id FROM insurance_payers WHERE id = :payer_id"), {"payer_id": request.payer_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Insurance payer not found")

        # Check for duplicate claim number
        res = conn.execute(text("SELECT id FROM claims WHERE claim_number = :cn"), {"cn": request.claim_number})
        if res.fetchone():
            raise HTTPException(status_code=409, detail="Claim number already exists")

        # Insert Claim
        insert_claim_sql = text("""
            INSERT INTO claims (claim_number, patient_id, policy_id, payer_id, total_billed_amount, status, submitted_at, submitted_by_user_id, notes, created_at, updated_at)
            VALUES (:claim_number, :patient_id, :policy_id, :payer_id, :total_billed_amount, 'submitted', :submitted_at, :submitted_by_user_id, :notes, :now, :now)
        """)
        result = conn.execute(insert_claim_sql, {
            "claim_number": request.claim_number,
            "patient_id": request.patient_id,
            "policy_id": request.policy_id,
            "payer_id": request.payer_id,
            "total_billed_amount": request.total_billed_amount,
            "submitted_at": now,
            "submitted_by_user_id": submitted_by_user_id,
            "notes": request.notes,
            "now": now
        })
        claim_id = result.lastrowid

        # Insert Procedures
        for proc in request.procedures:
            # Validate procedure code exists
            res = conn.execute(text("SELECT code FROM procedure_codes WHERE code = :code"), {"code": proc.procedure_code})
            if not res.fetchone():
                raise HTTPException(status_code=404, detail=f"Procedure code {proc.procedure_code} not found")

            insert_proc_sql = text("""
                INSERT INTO claim_procedures (claim_id, procedure_code, description, billed_amount, tooth_number, surface, created_at, updated_at)
                VALUES (:claim_id, :procedure_code, :description, :billed_amount, :tooth_number, :surface, :now, :now)
            """)
            conn.execute(insert_proc_sql, {
                "claim_id": claim_id,
                "procedure_code": proc.procedure_code,
                "description": proc.description,
                "billed_amount": proc.billed_amount,
                "tooth_number": proc.tooth_number,
                "surface": proc.surface,
                "now": now
            })

        # Fetch the created claim to return
        fetch_claim_sql = text("""
            SELECT id, claim_number, patient_id, policy_id, payer_id, total_billed_amount, status, 
                   submitted_at, submitted_by_user_id, notes, created_at, updated_at
            FROM claims WHERE id = :id
        """)
        row = conn.execute(fetch_claim_sql, {"id": claim_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created claim")

        response_data = {
            "id": row.id,
            "claim_number": row.claim_number,
            "patient_id": row.patient_id,
            "policy_id": row.policy_id,
            "payer_id": row.payer_id,
            "total_billed_amount": row.total_billed_amount,
            "status": row.status,
            "submitted_at": row.submitted_at,
            "submitted_by_user_id": row.submitted_by_user_id,
            "notes": row.notes,
            "created_at": row.created_at,
            "updated_at": row.updated_at
        }

    return response_data
