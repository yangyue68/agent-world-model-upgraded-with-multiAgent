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


class EventTypeCreate(BaseModel):
    name: str = Field(..., description="Name of the event type")
    duration_minutes: int = Field(..., gt=0, description="Duration of the event in minutes")
    location_type: str = Field(..., description="Type of location (e.g., 'google_meet')")
    location_value: str | None = Field(None, description="Value for the location (e.g., Google Meet link)")
    availability_days: int = Field(..., gt=0, description="Number of days in the future that the event can be booked")
    payment_required: bool = Field(..., description="Whether payment is required to book the event")
    payment_amount_cents: int | None = Field(None, ge=0, description="Amount to charge in cents")
    payment_currency: str | None = Field(None, description="Currency for the payment")
    payment_provider: str | None = Field(None, description="Payment provider (e.g., 'stripe')")
    reminder_enabled: bool = Field(..., description="Whether to enable automated reminder emails")
    reminder_minutes_before: int | None = Field(None, gt=0, description="Minutes before the event to send the reminder")


class EventTypeResponse(BaseModel):
    id: int
    name: str
    duration_minutes: int
    location_type: str
    location_value: str | None
    availability_days: int
    payment_required: bool
    payment_amount_cents: int | None
    payment_currency: str | None
    payment_provider: str | None
    reminder_enabled: bool
    reminder_minutes_before: int | None
    created_at: str
    updated_at: str


@app.post("/event_types", response_model=EventTypeResponse, operation_id="create_event_type")
def create_event_type(payload: EventTypeCreate):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context")

    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authenticated user context")

    # Validate user exists
    with engine.connect() as conn:
        user_row = conn.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": current_user_id}
        ).fetchone()
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    insert_sql = text("""
        INSERT INTO event_types (
            user_id,
            name,
            duration_minutes,
            location_type,
            location_value,
            availability_days,
            payment_required,
            payment_amount_cents,
            payment_currency,
            payment_provider,
            reminder_enabled,
            reminder_minutes_before,
            created_at,
            updated_at
        ) VALUES (
            :user_id,
            :name,
            :duration_minutes,
            :location_type,
            :location_value,
            :availability_days,
            :payment_required,
            :payment_amount_cents,
            :payment_currency,
            :payment_provider,
            :reminder_enabled,
            :reminder_minutes_before,
            :created_at,
            :updated_at
        )
    """)

    params = {
        "user_id": current_user_id,
        "name": payload.name,
        "duration_minutes": payload.duration_minutes,
        "location_type": payload.location_type,
        "location_value": payload.location_value,
        "availability_days": payload.availability_days,
        "payment_required": 1 if payload.payment_required else 0,
        "payment_amount_cents": payload.payment_amount_cents,
        "payment_currency": payload.payment_currency,
        "payment_provider": payload.payment_provider,
        "reminder_enabled": 1 if payload.reminder_enabled else 0,
        "reminder_minutes_before": payload.reminder_minutes_before,
        "created_at": now_utc,
        "updated_at": now_utc,
    }

    with engine.begin() as conn:
        result = conn.execute(insert_sql, params)
        new_id = result.lastrowid

        row = conn.execute(
            text("""
                SELECT
                    id,
                    name,
                    duration_minutes,
                    location_type,
                    location_value,
                    availability_days,
                    payment_required,
                    payment_amount_cents,
                    payment_currency,
                    payment_provider,
                    reminder_enabled,
                    reminder_minutes_before,
                    created_at,
                    updated_at
                FROM event_types
                WHERE id = :id
            """),
            {"id": new_id}
        ).fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created event type")

    return EventTypeResponse(
        id=row.id,
        name=row.name,
        duration_minutes=row.duration_minutes,
        location_type=row.location_type,
        location_value=row.location_value,
        availability_days=row.availability_days,
        payment_required=bool(row.payment_required),
        payment_amount_cents=row.payment_amount_cents,
        payment_currency=row.payment_currency,
        payment_provider=row.payment_provider,
        reminder_enabled=bool(row.reminder_enabled),
        reminder_minutes_before=row.reminder_minutes_before,
        created_at=str(row.created_at),
        updated_at=str(row.updated_at),
    )
