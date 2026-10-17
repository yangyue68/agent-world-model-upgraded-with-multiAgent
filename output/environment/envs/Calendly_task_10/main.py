import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Calendly Reminder Settings API")


# --- Pydantic Models ---

class ReminderSettingsResponse(BaseModel):
    id: int
    event_type_id: int
    hours_before: int
    include_meeting_link: bool
    custom_message: Optional[str] = None
    is_active: bool
    created_at: str
    updated_at: str

    class Config:
        orm_mode = True


class CreateReminderSettingsRequest(BaseModel):
    hours_before: int = Field(..., description="Number of hours before the event to send the reminder")
    include_meeting_link: bool = Field(..., description="Whether to include the meeting link in the reminder")
    custom_message: Optional[str] = Field(None, description="Custom message to include in the reminder email")


class UpdateReminderSettingsRequest(BaseModel):
    hours_before: Optional[int] = Field(None, description="Number of hours before the event to send the reminder")
    include_meeting_link: Optional[bool] = Field(None, description="Whether to include the meeting link in the reminder")
    custom_message: Optional[str] = Field(None, description="Custom message to include in the reminder email")


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def serialize_datetime(dt_obj) -> str:
    if dt_obj is None:
        return ""
    # Handle both string and datetime objects from SQLAlchemy depending on driver/config
    if isinstance(dt_obj, str):
        return dt_obj
    if hasattr(dt_obj, 'isoformat'):
        return dt_obj.isoformat(sep=' ')
    return str(dt_obj)


def row_to_response(row) -> dict:
    """Convert a SQLAlchemy Row object to a dictionary matching the response schema."""
    return {
        "id": row.id,
        "event_type_id": row.event_type_id,
        "hours_before": row.hours_before,
        "include_meeting_link": bool(row.include_meeting_link),
        "custom_message": row.custom_message,
        "is_active": bool(row.is_active),
        "created_at": serialize_datetime(row.created_at),
        "updated_at": serialize_datetime(row.updated_at)
    }


# --- Endpoints ---

@app.post("/event-types/{event_type_id}/reminder-settings", response_model=ReminderSettingsResponse, operation_id="create_reminder_settings")
async def create_reminder_settings(
    event_type_id: int = Path(..., description="Internal ID of the event type"),
    payload: CreateReminderSettingsRequest = Body(...)
):
    """
    Configure automated email reminders for a specific event type.
    The reminder will be sent X hours before the event start time, optionally including the meeting link and a custom message.
    """
    with engine.connect() as conn:
        # Check if event type exists
        result_et = conn.execute(text("SELECT id FROM event_types WHERE id = :eid"), {"eid": event_type_id})
        if result_et.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"Event type with ID {event_type_id} not found.")

        # Check if reminder settings already exist for this event type (UNIQUE constraint on event_type_id)
        result_rs = conn.execute(text("SELECT id FROM reminder_settings WHERE event_type_id = :eid"), {"eid": event_type_id})
        if result_rs.fetchone() is not None:
            raise HTTPException(status_code=409, detail=f"Reminder settings already exist for event type {event_type_id}. Use PUT to update.")

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_stmt = text("""
            INSERT INTO reminder_settings 
            (event_type_id, hours_before, include_meeting_link, custom_message, is_active, created_at, updated_at)
            VALUES 
            (:eid, :hb, :iml, :cm, 1, :now, :now)
        """)
        
        params = {
            "eid": event_type_id,
            "hb": payload.hours_before,
            "iml": 1 if payload.include_meeting_link else 0,
            "cm": payload.custom_message,
            "now": now_str
        }
        
        conn.execute(insert_stmt, params)
        conn.commit()

        # Fetch the newly created record
        select_stmt = text("SELECT * FROM reminder_settings WHERE event_type_id = :eid")
        final_row = conn.execute(select_stmt, {"eid": event_type_id}).fetchone()
        
        return row_to_response(final_row)


@app.get("/event-types/{event_type_id}/reminder-settings", response_model=Optional[ReminderSettingsResponse], operation_id="get_reminder_settings")
async def get_reminder_settings(
    event_type_id: int = Path(..., description="Internal ID of the event type")
):
    """
    Fetch the configured reminder settings for a given event type, including whether to include the meeting link and any custom message.
    """
    with engine.connect() as conn:
        # Verify event type exists first? Spec doesn't explicitly say 404 vs empty, but usually resource check is good practice.
        # However, returning null/empty if no settings exist is also common. Let's stick to fetching settings.
        # If we want strict REST, maybe 404 if ET missing. But let's just query RS.
        
        stmt = text("SELECT * FROM reminder_settings WHERE event_type_id = :eid")
        row = conn.execute(stmt, {"eid": event_type_id}).fetchone()
        
        if row is None:
            # Depending on interpretation, could return 404 or null. 
            # Given response model allows optional/null implicitly via structure, but FastAPI might complain if None returned for non-optional field.
            # The spec says response is an object. If it doesn't exist, typically 404.
            raise HTTPException(status_code=404, detail=f"No reminder settings found for event type {event_type_id}")
            
        return row_to_response(row)


@app.put("/event-types/{event_type_id}/reminder-settings", response_model=ReminderSettingsResponse, operation_id="update_reminder_settings")
async def update_reminder_settings(
    event_type_id: int = Path(..., description="Internal ID of the event type"),
    payload: UpdateReminderSettingsRequest = Body(...)
):
    """
    Modify existing reminder settings for a specific event type, such as changing the number of hours before the event to send the reminder or updating the custom message.
    """
    with engine.connect() as conn:
        # Check existence
        stmt_check = text("SELECT id FROM reminder_settings WHERE event_type_id = :eid")
        row_check = conn.execute(stmt_check, {"eid": event_type_id}).fetchone()
        
        if row_check is None:
            raise HTTPException(status_code=404, detail=f"No reminder settings found for event type {event_type_id}")

        updates = []
        params = {"eid": event_type_id}
        
        if payload.hours_before is not None:
            updates.append("hours_before = :hb")
            params["hb"] = payload.hours_before
            
        if payload.include_meeting_link is not None:
            updates.append("include_meeting_link = :iml")
            params["iml"] = 1 if payload.include_meeting_link else 0
            
        if payload.custom_message is not None:
            updates.append("custom_message = :cm")
            params["cm"] = payload.custom_message
        
        # Always update timestamp
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        updates.append("updated_at = :now")
        params["now"] = now_str

        if not updates:
             # No fields provided to update, just return current state
             pass
        else:
            sql_update = f"UPDATE reminder_settings SET {', '.join(updates)} WHERE event_type_id = :eid"
            conn.execute(text(sql_update), params)
            conn.commit()

        # Fetch updated record
        stmt_final = text("SELECT * FROM reminder_settings WHERE event_type_id = :eid")
        final_row = conn.execute(stmt_final, {"eid": event_type_id}).fetchone()
        
        return row_to_response(final_row)
