import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CircuitLogix API")


class DeliveryEventResponse(BaseModel):
    id: int
    stop_id: str
    route_id: str
    status: str
    event_timestamp: str
    signature_base64: Optional[str] = None
    photo_url: Optional[str] = None
    created_at: str
    updated_at: str


class UpdateDeliveryEventRequest(BaseModel):
    status: str = Field(..., enum=["delivered"])
    event_timestamp: str
    signature_base64: str
    photo_url: str


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both T...Z and space-separated forms."""
    dt_str = dt_str.strip()
    # Replace 'T' with ' ' if present for uniform parsing, then handle 'Z'
    if 'T' in dt_str:
        dt_str = dt_str.replace('T', ' ')
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    try:
        # Try parsing with timezone info first
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Fallback: assume UTC if no timezone
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def format_datetime(dt: datetime) -> str:
    """Format datetime to ISO-8601 string with Z suffix for UTC."""
    if dt.tzinfo is not None:
        # Convert to UTC and format
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        # Assume it's already UTC-like or naive; format without Z if naive
        return dt.strftime("%Y-%m-%dT%H:%M:%S")


@app.get("/delivery-events", response_model=DeliveryEventResponse, operation_id="find_delivery_event_by_stop_and_route")
def find_delivery_event_by_stop_and_route(
    stop_id: str = Query(..., description="Stop identifier"),
    route_id: str = Query(..., description="Route identifier")
):
    with SessionLocal() as db:
        result = db.execute(
            text("""
                SELECT id, stop_id, route_id, status, event_timestamp, 
                       signature_base64, photo_url, created_at, updated_at
                FROM delivery_events
                WHERE stop_id = :stop_id AND route_id = :route_id
            """),
            {"stop_id": stop_id, "route_id": route_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Delivery event not found")
        
        row = dict(result._mapping)
        
        # Format datetimes
        for field in ["event_timestamp", "created_at", "updated_at"]:
            val = row[field]
            if isinstance(val, str):
                # Already a string from DB, keep as-is but ensure consistent format
                pass
            elif isinstance(val, datetime):
                row[field] = format_datetime(val)
        
        return DeliveryEventResponse(**row)


@app.put("/delivery-events/{delivery_event_id}", response_model=DeliveryEventResponse, operation_id="update_delivery_event_status")
def update_delivery_event_status(
    delivery_event_id: int,
    body: UpdateDeliveryEventRequest
):
    # Validate status is 'delivered'
    if body.status != "delivered":
        raise HTTPException(status_code=400, detail="Status must be 'delivered'")
    
    # Parse the event timestamp
    try:
        parsed_ts = parse_datetime(body.event_timestamp)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid event_timestamp format")
    
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    
    with SessionLocal() as db:
        # First check if the delivery event exists
        existing = db.execute(
            text("SELECT id FROM delivery_events WHERE id = :id"),
            {"id": delivery_event_id}
        ).fetchone()
        
        if not existing:
            raise HTTPException(status_code=404, detail="Delivery event not found")
        
        # Update the delivery event
        now = datetime.now(timezone.utc)
        db.execute(
            text("""
                UPDATE delivery_events
                SET status = :status,
                    event_timestamp = :event_timestamp,
                    signature_base64 = :signature_base64,
                    photo_url = :photo_url,
                    updated_at = :updated_at
                WHERE id = :id
            """),
            {
                "id": delivery_event_id,
                "status": body.status,
                "event_timestamp": parsed_ts,
                "signature_base64": body.signature_base64,
                "photo_url": body.photo_url,
                "updated_at": now
            }
        )
        db.commit()
        
        # Fetch the updated record
        result = db.execute(
            text("""
                SELECT id, stop_id, route_id, status, event_timestamp, 
                       signature_base64, photo_url, created_at, updated_at
                FROM delivery_events
                WHERE id = :id
            """),
            {"id": delivery_event_id}
        ).fetchone()
        
        row = dict(result._mapping)
        
        # Format datetimes
        for field in ["event_timestamp", "created_at", "updated_at"]:
            val = row[field]
            if isinstance(val, datetime):
                row[field] = format_datetime(val)
            elif isinstance(val, str):
                # Ensure consistent formatting
                try:
                    dt = parse_datetime(val)
                    row[field] = format_datetime(dt)
                except Exception:
                    pass
        
        return DeliveryEventResponse(**row)
