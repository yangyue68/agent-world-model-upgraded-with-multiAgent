import os
from typing import Optional, Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class TreatmentRequest(BaseModel):
    mob_id: int = Field(..., description="Internal ID of the mob being treated")
    treatment_type: Literal["vaccination", "drench", "antibiotic", "other"] = Field(
        ..., description="Type of treatment"
    )
    product_name: str = Field(..., description="Name of the vaccine product used")
    dosage_per_head: float = Field(..., gt=0, description="Dosage amount per head in mL")
    treatment_date: str = Field(..., description="Date when treatment was administered (YYYY-MM-DD)")
    administered_by: str = Field(..., description="Name of the person administering the treatment")
    meat_withdrawal_days: int = Field(..., ge=0, description="Meat withdrawal period in days")


class TreatmentResponse(BaseModel):
    id: int
    mob_id: int
    treatment_type: str
    product_name: str
    dosage_per_head: float
    treatment_date: str
    administered_by: str
    meat_withdrawal_days: int
    created_by: int


@app.post("/treatments", response_model=TreatmentResponse, operation_id="record_vaccination_treatment")
def record_vaccination_treatment(request: TreatmentRequest):
    # Get current user from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="No authenticated user found")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    # Verify mob exists
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM mobs WHERE id = :mob_id"),
            {"mob_id": request.mob_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Mob with ID {request.mob_id} not found")

        # Insert treatment record
        insert_result = conn.execute(
            text("""
                INSERT INTO treatments (
                    mob_id, treatment_type, product_name, dosage_per_head, 
                    dosage_unit, treatment_date, administered_by, 
                    meat_withdrawal_days, created_by
                ) VALUES (
                    :mob_id, :treatment_type, :product_name, :dosage_per_head,
                    'mL', :treatment_date, :administered_by,
                    :meat_withdrawal_days, :created_by
                )
            """),
            {
                "mob_id": request.mob_id,
                "treatment_type": request.treatment_type,
                "product_name": request.product_name,
                "dosage_per_head": request.dosage_per_head,
                "treatment_date": request.treatment_date,
                "administered_by": request.administered_by,
                "meat_withdrawal_days": request.meat_withdrawal_days,
                "created_by": current_user_id,
            }
        )
        conn.commit()

        new_id = insert_result.lastrowid

        # Fetch the inserted record to return it
        fetched = conn.execute(
            text("""
                SELECT id, mob_id, treatment_type, product_name, dosage_per_head,
                       treatment_date, administered_by, meat_withdrawal_days, created_by
                FROM treatments WHERE id = :id
            """),
            {"id": new_id}
        ).fetchone()

        if not fetched:
            raise HTTPException(status_code=500, detail="Failed to retrieve inserted treatment")

        return TreatmentResponse(
            id=fetched[0],
            mob_id=fetched[1],
            treatment_type=fetched[2],
            product_name=fetched[3],
            dosage_per_head=float(fetched[4]),
            treatment_date=str(fetched[5]),
            administered_by=fetched[6],
            meat_withdrawal_days=int(fetched[7]),
            created_by=int(fetched[8]),
        )
