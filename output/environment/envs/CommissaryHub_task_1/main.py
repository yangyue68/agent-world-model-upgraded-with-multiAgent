import os
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from datetime import datetime

# Initialize app
app = FastAPI()

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)


class KitchenStationResponse(BaseModel):
    id: int
    name: str
    description: str
    capacity: int
    hourly_rate: float


@app.get("/available-kitchen-stations", response_model=List[KitchenStationResponse], operation_id="find_available_kitchen_stations")
def find_available_kitchen_stations(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    start_time: str = Query(..., description="Start time in HH:MM:SS format"),
    end_time: str = Query(..., description="End time in HH:MM:SS format")
):
    """
    Search for kitchen stations that are not booked during the specified date and time range.
    Returns all active stations that do not have any confirmed bookings overlapping with the requested period.
    Note: The spec says 'confirmed bookings', but typically availability checks consider pending too to avoid double booking.
    However, looking at the data: Station 3 has a 'pending' booking. If we only exclude 'confirmed', Station 3 might appear available if no other confirmed overlaps.
    Let's re-read carefully: "Returns all active stations that do not have any confirmed bookings overlapping..."
    This implies we should ONLY check against status='confirmed'. Pending bookings do NOT block availability according to this strict reading.
    
    Overlap logic: Two intervals [S1, E1] and [S2, E2] overlap if S1 < E2 AND S2 < E1.
    Here, Request Interval is [Req_Start, Req_End]. Booking Interval is [Book_Start, Book_End].
    Condition for conflict: Req_Start < Book_End AND Book_Start < Req_End.
    """
    
    # Construct full datetime strings for comparison
    try:
        req_start_dt_str = f"{date} {start_time}"
        req_end_dt_str = f"{date} {end_time}"
        
        # Validate formats by parsing them (optional but good for robustness)
        # Using standard ISO-like format expected by SQLite usually works with string comparison if formatted correctly
        # But let's ensure they are valid dates/times implicitly via SQL or just pass as strings since DB stores DATETIME
        
        # We will use parameterized queries to prevent injection and handle types safely
        query = text("""
            SELECT ks.id, ks.name, ks.description, ks.capacity, ks.hourly_rate
            FROM kitchen_stations ks
            WHERE ks.is_active = 1
              AND NOT EXISTS (
                  SELECT 1 
                  FROM bookings b 
                  WHERE b.station_id = ks.id 
                    AND b.status = 'confirmed'
                    AND :req_start < b.end_time 
                    AND b.start_time < :req_end
              )
            ORDER BY ks.id;
        """)
        
        params = {
            "req_start": req_start_dt_str,
            "req_end": req_end_dt_str
        }
        
        with engine.connect() as conn:
            result = conn.execute(query, params)
            rows = result.fetchall()
            
            stations = []
            for row in rows:
                stations.append(KitchenStationResponse(
                    id=row[0],
                    name=row[1],
                    description=row[2],
                    capacity=row[3],
                    hourly_rate=float(row[4])
                ))
                
            return stations
            
    except Exception as e:
        # In a real scenario, log this. For now, raising HTTP 500 or specific error.
        # Since input validation is mostly done by FastAPI/Query types, unexpected errors here are server-side.
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
