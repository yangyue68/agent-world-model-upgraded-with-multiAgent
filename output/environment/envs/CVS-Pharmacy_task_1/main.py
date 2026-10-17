import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RefillRequest(BaseModel):
    prescription_id: int = Field(..., description="Internal ID of the prescription to refill")
    quantity: int = Field(..., description="Number of tablets to refill")
    pharmacy_id: int = Field(..., description="Internal ID of the pharmacy where pickup will occur")
    pickup_type: str = Field(default="in-store", description="Type of pickup method (in-store, drive-thru, delivery)")


class RefillResponse(BaseModel):
    id: int
    prescription_id: int
    user_id: int
    pharmacy_id: int
    quantity: int
    pickup_type: str
    status: str
    created_at: str
    updated_at: str


@app.post("/refill-prescriptions", response_model=RefillResponse, operation_id="create_refill_order")
def create_refill_order(payload: RefillRequest):
    # Get current user from environment
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Validate prescription exists and belongs to user
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, user_id, refills_remaining, status 
                FROM prescriptions 
                WHERE id = :prescription_id AND user_id = :user_id
            """),
            {"prescription_id": payload.prescription_id, "user_id": user_id}
        )
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Prescription not found or does not belong to user")
        
        presc_id, presc_user_id, refills_remaining, presc_status = row
        
        if presc_status != 'active':
            raise HTTPException(status_code=400, detail="Prescription is not active")
        
        if refills_remaining <= 0:
            raise HTTPException(status_code=400, detail="No refills remaining for this prescription")

        # Validate pharmacy exists
        pharm_result = conn.execute(
            text("SELECT id FROM pharmacies WHERE id = :pharmacy_id"),
            {"pharmacy_id": payload.pharmacy_id}
        )
        if not pharm_result.fetchone():
            raise HTTPException(status_code=404, detail="Pharmacy not found")

        # Insert refill order
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_result = conn.execute(
            text("""
                INSERT INTO refill_orders (prescription_id, user_id, pharmacy_id, quantity, pickup_type, status, created_at, updated_at)
                VALUES (:prescription_id, :user_id, :pharmacy_id, :quantity, :pickup_type, :status, :created_at, :updated_at)
            """),
            {
                "prescription_id": payload.prescription_id,
                "user_id": user_id,
                "pharmacy_id": payload.pharmacy_id,
                "quantity": payload.quantity,
                "pickup_type": payload.pickup_type,
                "status": "pending",
                "created_at": now,
                "updated_at": now
            }
        )
        
        new_id = insert_result.lastrowid
        
        # Update prescription refills_remaining
        conn.execute(
            text("""
                UPDATE prescriptions 
                SET refills_remaining = refills_remaining - 1, updated_at = :updated_at
                WHERE id = :prescription_id
            """),
            {"prescription_id": payload.prescription_id, "updated_at": now}
        )
        
        conn.commit()

        # Fetch the created record to return full details
        final_result = conn.execute(
            text("""
                SELECT id, prescription_id, user_id, pharmacy_id, quantity, pickup_type, status, created_at, updated_at
                FROM refill_orders
                WHERE id = :id
            """),
            {"id": new_id}
        )
        final_row = final_result.fetchone()
        
        if not final_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created refill order")

        return RefillResponse(
            id=final_row[0],
            prescription_id=final_row[1],
            user_id=final_row[2],
            pharmacy_id=final_row[3],
            quantity=final_row[4],
            pickup_type=final_row[5],
            status=final_row[6],
            created_at=str(final_row[7]),
            updated_at=str(final_row[8])
        )
