import os
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from typing import List, Optional

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AvailabilitySlot(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)
    start_time: str
    end_time: str
    session_duration_minutes: int = Field(..., gt=0)
    is_blocked: bool = False


class UpdateAvailabilityRequest(BaseModel):
    slots: List[AvailabilitySlot]


class BulkDeleteRequest(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)


class MessageResponse(BaseModel):
    message: str


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")


@app.put("/availability", response_model=MessageResponse, operation_id="update_availability")
def update_availability(request: UpdateAvailabilityRequest):
    user_id = get_current_user_id()

    with engine.connect() as conn:
        # Verify user exists
        result = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Delete all existing availability slots for this user
        conn.execute(
            text("DELETE FROM availability_slots WHERE user_id = :uid"),
            {"uid": user_id}
        )

        # Insert new slots
        for slot in request.slots:
            conn.execute(
                text("""
                    INSERT INTO availability_slots 
                    (user_id, day_of_week, start_time, end_time, session_duration_minutes, is_blocked)
                    VALUES (:uid, :dow, :st, :et, :sdm, :ib)
                """),
                {
                    "uid": user_id,
                    "dow": slot.day_of_week,
                    "st": slot.start_time,
                    "et": slot.end_time,
                    "sdm": slot.session_duration_minutes,
                    "ib": 1 if slot.is_blocked else 0
                }
            )

        conn.commit()

    return {"message": "Availability updated successfully"}


@app.delete("/availability/{slot_id}", response_model=MessageResponse, operation_id="delete_availability_slot")
def delete_availability_slot(slot_id: int = Path(...)):
    user_id = get_current_user_id()

    with engine.connect() as conn:
        # Verify the slot belongs to the current user
        result = conn.execute(
            text("SELECT id FROM availability_slots WHERE id = :sid AND user_id = :uid"),
            {"sid": slot_id, "uid": user_id}
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Availability slot not found or does not belong to user")

        conn.execute(
            text("DELETE FROM availability_slots WHERE id = :sid"),
            {"sid": slot_id}
        )
        conn.commit()

    return {"message": "Availability slot deleted successfully"}


@app.post("/availability/bulk_delete", response_model=MessageResponse, operation_id="bulk_delete_availability_slots")
def bulk_delete_availability_slots(request: BulkDeleteRequest):
    user_id = get_current_user_id()

    with engine.connect() as conn:
        # Verify user exists
        result = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        conn.execute(
            text("DELETE FROM availability_slots WHERE user_id = :uid AND day_of_week = :dow"),
            {"uid": user_id, "dow": request.day_of_week}
        )
        conn.commit()

    return {"message": f"All availability slots for day {request.day_of_week} deleted successfully"}
