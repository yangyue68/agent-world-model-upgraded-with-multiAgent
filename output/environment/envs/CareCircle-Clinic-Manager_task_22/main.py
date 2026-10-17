from sqlalchemy import Column
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Integer, String, Boolean
from sqlalchemy.orm import declarative_base, Session

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
Base = declarative_base()


class InsuranceProvider(Base):
    __tablename__ = "insurance_providers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(String)
    updated_at = Column(String)


class PatientInsurancePolicy(Base):
    __tablename__ = "patient_insurance_policies"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, nullable=False)
    provider_id = Column(Integer, nullable=False)
    policy_number = Column(String, nullable=False)
    is_primary = Column(Boolean, default=False)
    created_at = Column(String)
    updated_at = Column(String)


class ProviderResponse(BaseModel):
    id: int
    name: str


class PolicyCreateRequest(BaseModel):
    provider_id: int
    policy_number: str
    is_primary: bool = False


class PolicyResponse(BaseModel):
    id: int
    patient_id: int
    provider_id: int
    policy_number: str
    is_primary: bool


app = FastAPI(title="CareCircle Clinic Manager API")


@app.get("/insurance_providers", response_model=list[ProviderResponse], operation_id="list_insurance_providers")
def list_insurance_providers():
    """List all insurance providers."""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, name FROM insurance_providers ORDER BY id"))
        rows = result.fetchall()
        return [ProviderResponse(id=row.id, name=row.name) for row in rows]


@app.post(
    "/patients/{patient_id}/insurance_policies",
    response_model=PolicyResponse,
    operation_id="add_patient_insurance_policy",
    status_code=201,
)
def add_patient_insurance_policy(
    patient_id: int = Path(..., description="Internal ID of the patient"),
    body: PolicyCreateRequest = Body(...),
):
    """Add a new insurance policy for a patient."""
    
    # Validate patient exists
    with engine.connect() as conn:
        patient_check = conn.execute(
            text("SELECT id FROM patients WHERE id = :pid"),
            {"pid": patient_id}
        ).fetchone()
        
        if not patient_check:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
        
        # Validate provider exists
        provider_check = conn.execute(
            text("SELECT id FROM insurance_providers WHERE id = :prov_id"),
            {"prov_id": body.provider_id}
        ).fetchone()
        
        if not provider_check:
            raise HTTPException(status_code=404, detail=f"Insurance provider {body.provider_id} not found")
        
        # If marking as primary, demote any existing primary policy for this patient
        if body.is_primary:
            conn.execute(
                text("""
                    UPDATE patient_insurance_policies 
                    SET is_primary = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE patient_id = :pid AND is_primary = 1
                """),
                {"pid": patient_id}
            )
        
        # Insert the new policy
        result = conn.execute(
            text("""
                INSERT INTO patient_insurance_policies (patient_id, provider_id, policy_number, is_primary, created_at, updated_at)
                VALUES (:pid, :prov_id, :pol_num, :primary, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "pid": patient_id,
                "prov_id": body.provider_id,
                "pol_num": body.policy_number,
                "primary": 1 if body.is_primary else 0,
            }
        )
        conn.commit()
        
        new_id = result.lastrowid
        
        # Fetch and return the created record
        final_row = conn.execute(
            text("""
                SELECT id, patient_id, provider_id, policy_number, is_primary 
                FROM patient_insurance_policies 
                WHERE id = :new_id
            """),
            {"new_id": new_id}
        ).fetchone()
        
        return PolicyResponse(
            id=final_row.id,
            patient_id=final_row.patient_id,
            provider_id=final_row.provider_id,
            policy_number=final_row.policy_number,
            is_primary=bool(final_row.is_primary)
        )
