import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from typing import Optional, List

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CustomQuestion(BaseModel):
    question_text: str
    question_type: str
    is_required: bool = False


class EventTypeCreateRequest(BaseModel):
    name: str
    duration_minutes: int
    location: Optional[str] = None
    availability_synced_to_google_calendar: Optional[bool] = None
    buffer_before_minutes: Optional[int] = None
    buffer_after_minutes: Optional[int] = None
    max_bookings_per_day: Optional[int] = None
    custom_questions: Optional[List[CustomQuestion]] = None


class EventTypeResponse(BaseModel):
    id: int
    name: str
    duration_minutes: int
    location: Optional[str] = None
    availability_synced_to_google_calendar: Optional[bool] = None
    buffer_before_minutes: Optional[int] = None
    buffer_after_minutes: Optional[int] = None
    max_bookings_per_day: Optional[int] = None
    created_at: str
    updated_at: str


@app.post("/event_types", response_model=EventTypeResponse, operation_id="create_event_type")
def create_event_type(payload: EventTypeCreateRequest):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    # Validate user exists
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Prepare event type data
    avail_sync = 1 if payload.availability_synced_to_google_calendar else 0
    buf_before = payload.buffer_before_minutes if payload.buffer_before_minutes is not None else 0
    buf_after = payload.buffer_after_minutes if payload.buffer_after_minutes is not None else 0
    
    insert_event_sql = """
        INSERT INTO event_types (
            user_id, 
            name, 
            duration_minutes, 
            location, 
            availability_synced_to_google_calendar, 
            buffer_before_minutes, 
            buffer_after_minutes, 
            max_bookings_per_day, 
            created_at, 
            updated_at
        ) VALUES (
            :user_id, 
            :name, 
            :duration_minutes, 
            :location, 
            :avail_sync, 
            :buf_before, 
            :buf_after, 
            :max_bookings, 
            :now, 
            :now
        )
    """
    
    params = {
        "user_id": user_id,
        "name": payload.name,
        "duration_minutes": payload.duration_minutes,
        "location": payload.location,
        "avail_sync": avail_sync,
        "buf_before": buf_before,
        "buf_after": buf_after,
        "max_bookings": payload.max_bookings_per_day,
        "now": now
    }

    with engine.begin() as conn:
        res = conn.execute(text(insert_event_sql), params)
        event_type_id = res.lastrowid
        
        # Insert custom questions if provided
        if payload.custom_questions:
            for idx, q in enumerate(payload.custom_questions, start=1):
                req_val = 1 if q.is_required else 0
                insert_q_sql = """
                    INSERT INTO custom_questions (
                        event_type_id, 
                        question_text, 
                        question_type, 
                        is_required, 
                        display_order, 
                        created_at, 
                        updated_at
                    ) VALUES (
                        :eid, 
                        :qtext, 
                        :qtype, 
                        :req, 
                        :order, 
                        :now, 
                        :now
                    )
                """
                q_params = {
                    "eid": event_type_id,
                    "qtext": q.question_text,
                    "qtype": q.question_type,
                    "req": req_val,
                    "order": idx,
                    "now": now
                }
                conn.execute(text(insert_q_sql), q_params)

        # Fetch the created event type to return full details including defaults applied by DB if any
        fetch_sql = """
            SELECT 
                id, 
                name, 
                duration_minutes, 
                location, 
                availability_synced_to_google_calendar, 
                buffer_before_minutes, 
                buffer_after_minutes, 
                max_bookings_per_day, 
                created_at, 
                updated_at
            FROM event_types 
            WHERE id = :id
        """
        row = conn.execute(text(fetch_sql), {"id": event_type_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created event type")

        # Map boolean integers back to Python booleans/None appropriately based on schema expectations
        # The response model expects Optional[bool] for availability_synced_to_google_calendar
        # But typically APIs return true/false. Let's stick to what was sent or default logic.
        # However, since we inserted explicitly, we can just use those values or re-read.
        # Re-reading ensures consistency with DB state.
        
        db_avail_sync = bool(row[4])
        
        response_data = {
            "id": row[0],
            "name": row[1],
            "duration_minutes": row[2],
            "location": row[3],
            "availability_synced_to_google_calendar": db_avail_sync,
            "buffer_before_minutes": row[5],
            "buffer_after_minutes": row[6],
            "max_bookings_per_day": row[7],
            "created_at": row[8],
            "updated_at": row[9]
        }
        
        return EventTypeResponse(**response_data)
