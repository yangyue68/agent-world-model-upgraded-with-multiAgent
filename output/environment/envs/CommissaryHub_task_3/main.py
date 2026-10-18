import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AvailabilityResponse(BaseModel):
    available: bool
    hourly_rate: Optional[float] = None
    equipment_id: Optional[int] = None


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, supporting both 'T' separator and space."""
    # Replace T with space for uniform parsing if needed, but fromisoformat handles T in Python 3.7+
    # However, some formats might have Z or other suffixes. Let's clean it up.
    dt_str = dt_str.strip()
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Fallback for space-separated format like "2025-03-20 09:00:00"
        # fromisoformat should handle this too in newer Python versions, 
        # but let's be safe with a manual replace if it fails.
        try:
            normalized = dt_str.replace(' ', 'T')
            return datetime.fromisoformat(normalized)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid datetime format: {dt_str}")


@app.get("/equipment/availability", response_model=AvailabilityResponse, operation_id="check_equipment_availability")
async def check_equipment_availability(
    equipment_name: str = Query(..., description="Name of the equipment to check"),
    start_time: str = Query(..., description="Start datetime in ISO format (e.g., 2025-03-20T09:00:00)"),
    end_time: str = Query(..., description="End datetime in ISO format (e.g., 2025-03-20T12:00:00)")
):
    # Parse datetimes
    start_dt = parse_iso_datetime(start_time)
    end_dt = parse_iso_datetime(end_time)
    
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start_time must be before end_time")

    formatted_start = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    formatted_end = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    with engine.connect() as conn:
        # 1. Find the equipment by name and ensure it is active
        eq_query = text("""
            SELECT id, hourly_rate 
            FROM equipment 
            WHERE name = :name AND is_active = 1
        """)
        eq_result = conn.execute(eq_query, {"name": equipment_name}).fetchone()
        
        if not eq_result:
            # Equipment not found or inactive
            return AvailabilityResponse(available=False, hourly_rate=None, equipment_id=None)
        
        equipment_id = eq_result[0]
        hourly_rate = eq_result[1]

        # 2. Check for overlapping confirmed bookings
        booking_overlap_query = text("""
            SELECT COUNT(*) 
            FROM equipment_bookings 
            WHERE equipment_id = :eq_id 
              AND status = 'confirmed'
              AND start_time < :end_time 
              AND end_time > :start_time
        """)
        booking_count = conn.execute(booking_overlap_query, {
            "eq_id": equipment_id,
            "start_time": formatted_start,
            "end_time": formatted_end
        }).scalar()

        if booking_count > 0:
            return AvailabilityResponse(available=False, hourly_rate=None, equipment_id=equipment_id)

        # 3. Check availability windows in equipment_availability table
        # The logic here depends on how availability is modeled. 
        # Looking at the schema: equipment_availability has is_available flag.
        # Usually, we want to see if there is an explicit block where is_available = 0 covering the requested time?
        # Or does the existence of a row with is_available=1 mean it IS available?
        # Given the data snapshot:
        # Row 1: Blast Chiller (id 1), 2025-03-20 08:00:00 to 18:00:00, is_available=1.
        # This suggests that rows define periods where the equipment is marked as available/unavailable.
        # If no row exists, what is the default? 
        # Typically, if there are specific availability slots defined, we might need to intersect.
        # However, often "availability" tables track *blocks* of unavailability OR blocks of availability.
        # Let's look at the constraint: `is_available INTEGER NOT NULL DEFAULT 1`.
        # And the example policy says "Returns hourly rate if available".
        
        # Hypothesis A: Rows represent times when the equipment is explicitly scheduled/marked.
        # If is_available=1, it means it CAN be booked during that window (subject to actual bookings).
        # If is_available=0, it means it CANNOT be booked (maintenance etc.).
        # If NO row covers the requested time, is it available? 
        # In many systems, absence of a restriction implies availability. But here we have positive assertions of availability.
        # Let's assume that if there is a conflicting record with is_available=0, it's unavailable.
        # What if there is no record? 
        # Let's check if there are any records with is_available=0 overlapping the request.
        
        avail_block_query = text("""
            SELECT COUNT(*) 
            FROM equipment_availability 
            WHERE equipment_id = :eq_id 
              AND is_available = 0
              AND start_time < :end_time 
              AND end_time > :start_time
        """)
        blocked_count = conn.execute(avail_block_query, {
            "eq_id": equipment_id,
            "start_time": formatted_start,
            "end_time": formatted_end
        }).scalar()

        if blocked_count > 0:
            return AvailabilityResponse(available=False, hourly_rate=None, equipment_id=equipment_id)

        # Now, do we require an explicit is_available=1 record?
        # If the system relies on these records to define open hours, then yes.
        # If the system defaults to open unless closed, then no.
        # Given the task "Check the availability... and if available, note the hourly rate",
        # and looking at typical commissary/hub scenarios, usually you book within operating hours.
        # The presence of `equipment_availability` with `is_available=1` strongly suggests these are the valid booking windows.
        # Therefore, we should verify that the requested time falls WITHIN at least one window where is_available=1.
        
        valid_window_query = text("""
            SELECT COUNT(*) 
            FROM equipment_availability 
            WHERE equipment_id = :eq_id 
              AND is_available = 1
              AND start_time <= :start_time 
              AND end_time >= :end_time
        """)
        valid_window_count = conn.execute(valid_window_query, {
            "eq_id": equipment_id,
            "start_time": formatted_start,
            "end_time": formatted_end
        }).scalar()

        if valid_window_count == 0:
            # No explicit availability window covers the entire requested duration
            return AvailabilityResponse(available=False, hourly_rate=None, equipment_id=equipment_id)

        # If we reach here, it's available
        return AvailabilityResponse(available=True, hourly_rate=hourly_rate, equipment_id=equipment_id)
