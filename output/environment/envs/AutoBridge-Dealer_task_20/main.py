import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="AutoBridge Dealer API")


class LeadCreateRequest(BaseModel):
    customer_name: str = Field(..., description="Full name of the customer")
    customer_phone: str = Field(..., description="Phone number of the customer")
    customer_email: str | None = Field(None, description="Email address of the customer")
    vehicle_interest: str | None = Field(None, description="Specific vehicle the customer is interested in")
    source: str = Field(..., description="Source of the lead (e.g., Walk-in, Phone, Website)")
    assigned_to: int = Field(..., description="ID of the salesperson to whom the lead is assigned")


class LeadResponse(BaseModel):
    id: int
    customer_name: str
    customer_phone: str
    customer_email: str | None
    vehicle_interest: str | None
    source: str
    status: str
    assigned_to: int | None
    created_by: int
    created_at: str
    updated_at: str


VALID_SOURCES = {"Walk-in", "Phone", "Website", "Referral", "Social Media", "Email", "Other"}


@app.post("/leads", response_model=LeadResponse, operation_id="create_lead")
def create_lead(payload: LeadCreateRequest):
    if payload.source not in VALID_SOURCES:
        raise HTTPException(status_code=400, detail=f"Invalid source. Must be one of {sorted(VALID_SOURCES)}")

    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=500, detail="Current user ID not set in environment")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID format")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        # Verify assigned_to exists and is a valid user
        result = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": payload.assigned_to})
        if result.fetchone() is None:
            raise HTTPException(status_code=400, detail=f"User with ID {payload.assigned_to} does not exist")

        # Insert new lead
        insert_result = conn.execute(
            text("""
                INSERT INTO leads (
                    customer_name, customer_phone, customer_email, vehicle_interest,
                    source, status, assigned_to, created_by, created_at, updated_at
                ) VALUES (
                    :customer_name, :customer_phone, :customer_email, :vehicle_interest,
                    :source, 'new', :assigned_to, :created_by, :created_at, :updated_at
                )
            """),
            {
                "customer_name": payload.customer_name,
                "customer_phone": payload.customer_phone,
                "customer_email": payload.customer_email,
                "vehicle_interest": payload.vehicle_interest,
                "source": payload.source,
                "assigned_to": payload.assigned_to,
                "created_by": current_user_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        lead_id = insert_result.lastrowid

        # Fetch the created lead to return full details
        row = conn.execute(
            text("""
                SELECT id, customer_name, customer_phone, customer_email, vehicle_interest,
                       source, status, assigned_to, created_by, created_at, updated_at
                FROM leads WHERE id = :lid
            """),
            {"lid": lead_id},
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve created lead")

    return LeadResponse(
        id=row[0],
        customer_name=row[1],
        customer_phone=row[2],
        customer_email=row[3],
        vehicle_interest=row[4],
        source=row[5],
        status=row[6],
        assigned_to=row[7],
        created_by=row[8],
        created_at=str(row[9]),
        updated_at=str(row[10]),
    )
