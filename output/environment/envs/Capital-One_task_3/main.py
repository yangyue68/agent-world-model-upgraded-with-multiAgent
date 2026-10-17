import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from typing import Optional

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RefinanceApplicationRequest(BaseModel):
    requested_amount: float = Field(..., gt=0)
    term_months: int = Field(..., gt=0)
    target_monthly_payment: Optional[float] = None
    first_payment_date: str = Field(...)


@app.post("/refinance_applications", operation_id="submit_refinance_application")
def submit_refinance_application(
    vehicle_vin: str = Query(..., description="VIN of the vehicle to refinance"),
    payload: RefinanceApplicationRequest = ...,
):
    # Get current user ID from environment
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Validate target_monthly_payment if provided
    if payload.target_monthly_payment is not None and payload.target_monthly_payment <= 0:
        raise HTTPException(status_code=422, detail="target_monthly_payment must be greater than 0")

    with engine.connect() as conn:
        # Find vehicle by VIN
        result = conn.execute(
            text("SELECT id FROM vehicles WHERE vin = :vin"),
            {"vin": vehicle_vin}
        )
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Vehicle with VIN {vehicle_vin} not found")
        
        vehicle_id = row[0]

        # Insert new application
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_result = conn.execute(
            text("""
                INSERT INTO refinance_applications (
                    user_id, 
                    vehicle_id, 
                    requested_amount, 
                    term_months, 
                    target_monthly_payment, 
                    first_payment_date, 
                    status, 
                    submitted_at, 
                    created_at, 
                    updated_at
                ) VALUES (
                    :user_id, 
                    :vehicle_id, 
                    :requested_amount, 
                    :term_months, 
                    :target_monthly_payment, 
                    :first_payment_date, 
                    'submitted', 
                    :now, 
                    :now, 
                    :now
                )
            """),
            {
                "user_id": user_id,
                "vehicle_id": vehicle_id,
                "requested_amount": payload.requested_amount,
                "term_months": payload.term_months,
                "target_monthly_payment": payload.target_monthly_payment,
                "first_payment_date": payload.first_payment_date,
                "now": now
            }
        )
        
        app_id = insert_result.lastrowid
        
        # Fetch the inserted record to return full details
        final_result = conn.execute(
            text("""
                SELECT id, user_id, vehicle_id, requested_amount, term_months, 
                       target_monthly_payment, first_payment_date, status, 
                       submitted_at, created_at, updated_at
                FROM refinance_applications
                WHERE id = :id
            """),
            {"id": app_id}
        )
        
        record = final_result.fetchone()
        
        if not record:
            raise HTTPException(status_code=500, detail="Failed to retrieve created application")

        response_data = {
            "id": record[0],
            "user_id": record[1],
            "vehicle_id": record[2],
            "requested_amount": record[3],
            "term_months": record[4],
            "target_monthly_payment": record[5],
            "first_payment_date": record[6],
            "status": record[7],
            "submitted_at": record[8].isoformat() if hasattr(record[8], 'isoformat') else str(record[8]),
            "created_at": record[9].isoformat() if hasattr(record[9], 'isoformat') else str(record[9]),
            "updated_at": record[10].isoformat() if hasattr(record[10], 'isoformat') else str(record[10])
        }
        
        return response_data
