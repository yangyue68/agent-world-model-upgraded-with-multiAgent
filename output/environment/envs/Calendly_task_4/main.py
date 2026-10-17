import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UpdateEventTypeRequest(BaseModel):
    max_bookings_per_day: int
    buffer_before_minutes: int
    buffer_after_minutes: int


class EventTypeResponse(BaseModel):
    id: int
    name: str
    max_bookings_per_day: int
    buffer_before_minutes: int
    buffer_after_minutes: int


@app.put("/event-types/{event_type_id}", response_model=EventTypeResponse, operation_id="update_event_type_booking_limits")
def update_event_type_booking_limits(
    event_type_id: int,
    payload: UpdateEventTypeRequest,
):
    # Validate input constraints based on schema CHECKs
    if payload.max_bookings_per_day <= 0:
        raise HTTPException(status_code=422, detail="max_bookings_per_day must be greater than 0")
    if payload.buffer_before_minutes < 0:
        raise HTTPException(status_code=422, detail="buffer_before_minutes cannot be negative")
    if payload.buffer_after_minutes < 0:
        raise HTTPException(status_code=422, detail="buffer_after_minutes cannot be negative")

    with engine.begin() as conn:
        # Check existence
        row = conn.execute(
            text("SELECT id FROM event_types WHERE id = :id"),
            {"id": event_type_id}
        ).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Event type not found")

        # Perform update
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        conn.execute(
            text("""
                UPDATE event_types 
                SET max_bookings_per_day = :max_bookings,
                    buffer_before_minutes = :buf_before,
                    buffer_after_minutes = :buf_after,
                    updated_at = :now
                WHERE id = :id
            """),
            {
                "max_bookings": payload.max_bookings_per_day,
                "buf_before": payload.buffer_before_minutes,
                "buf_after": payload.buffer_after_minutes,
                "now": now_str,
                "id": event_type_id
            }
        )

        # Fetch updated data to return
        result = conn.execute(
            text("""
                SELECT id, name, max_bookings_per_day, buffer_before_minutes, buffer_after_minutes
                FROM event_types
                WHERE id = :id
            """),
            {"id": event_type_id}
        ).fetchone()

        if not result:
             # Should theoretically not happen since we just updated it, but safe guard
            raise HTTPException(status_code=500, detail="Failed to retrieve updated event type")

        return EventTypeResponse(
            id=result.id,
            name=result.name,
            max_bookings_per_day=result.max_bookings_per_day,
            buffer_before_minutes=result.buffer_before_minutes,
            buffer_after_minutes=result.buffer_after_minutes
        )
