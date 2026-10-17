import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AuctionLotResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    category_id: int
    condition: str
    location_id: int
    unit_count: int
    auction_end_time: str
    distance_miles: float


class LocationResponse(BaseModel):
    id: int
    zip_code: str
    city: str
    state: str
    latitude: float
    longitude: float


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance in miles between two points on earth."""
    from math import radians, sin, cos, sqrt, atan2

    R = 3958.8  # Earth radius in miles

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


@app.get("/locations/{zip_code}", response_model=LocationResponse, operation_id="get_location_by_zip")
def get_location_by_zip(zip_code: str):
    with Session(engine) as session:
        result = session.execute(
            text("SELECT id, zip_code, city, state, latitude, longitude FROM locations WHERE zip_code = :zip"),
            {"zip": zip_code},
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Location not found")

        return LocationResponse(
            id=result[0],
            zip_code=result[1],
            city=result[2],
            state=result[3],
            latitude=result[4],
            longitude=result[5],
        )


@app.get("/auction_lots/filter", response_model=List[AuctionLotResponse], operation_id="filter_auction_lots")
def filter_auction_lots(
    zip_code: str = Query(..., description="Target zip code for location filtering"),
    category_name: str = Query(..., description="Category name to filter by"),
    condition: str = Query(..., description="Lot condition to filter by"),
    min_units: int = Query(10, description="Minimum number of units (default 10)"),
):
    # Get target location coordinates
    with Session(engine) as session:
        loc_result = session.execute(
            text("SELECT latitude, longitude FROM locations WHERE zip_code = :zip"),
            {"zip": zip_code},
        ).fetchone()

        if not loc_result:
            raise HTTPException(status_code=404, detail="Zip code not found")

        target_lat, target_lon = loc_result[0], loc_result[1]

        # Get category ID
        cat_result = session.execute(
            text("SELECT id FROM categories WHERE name = :name"),
            {"name": category_name},
        ).fetchone()

        if not cat_result:
            raise HTTPException(status_code=404, detail="Category not found")

        category_id = cat_result[0]

        # Filter lots by category, condition, and minimum units
        # We also need to check time remaining (< 24 hours) and distance (< 300 miles)
        # Since SQLite doesn't have built-in geo functions, we fetch candidates and filter in Python
        
        query = text("""
            SELECT al.id, al.title, al.description, al.category_id, al.condition, 
                   al.location_id, al.unit_count, al.auction_end_time, l.latitude, l.longitude
            FROM auction_lots al
            JOIN locations l ON al.location_id = l.id
            WHERE al.category_id = :cat_id
              AND al.condition = :cond
              AND al.unit_count >= :min_u
        """)

        rows = session.execute(query, {
            "cat_id": category_id,
            "cond": condition,
            "min_u": min_units,
        }).fetchall()

        results = []
        current_time_str = None
        
        # Determine current time for comparison. 
        # In a real app, this would be datetime.now(). Here we assume the DB is static relative to 'now' 
        # or we just compare against a fixed reference if needed. 
        # However, the task says "less than 24 hours remaining". 
        # The data has dates like 2026-10-09. If we run this now (2024/2025), all are > 24h.
        # But usually these tasks imply checking against the system clock or a simulated clock.
        # Let's use Python's datetime to get current UTC time.
        
        from datetime import datetime, timezone
        
        now_utc = datetime.now(timezone.utc)
        
        for row in rows:
            lot_id, title, desc, cat_id, cond, loc_id, units, end_time_str, lat, lon = row
            
            # Parse auction_end_time
            # Format in DB seems to be "YYYY-MM-DD HH:MM:SS"
            try:
                # Handle potential timezone info if present, otherwise assume UTC
                if 'T' in end_time_str or '+' in end_time_str or 'Z' in end_time_str:
                    # ISO format handling
                    dt_end = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                else:
                    dt_end = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
                    dt_end = dt_end.replace(tzinfo=timezone.utc)
                
                # Calculate time difference
                diff_seconds = (dt_end - now_utc).total_seconds()
                
                # Check if less than 24 hours remaining (and positive, i.e., not ended yet? 
                # Spec says "remaining", usually implies future. If negative, it's past.)
                # "Less than 24 hours remaining" typically means 0 < time_left < 24h.
                # If the auction has already ended, there are no hours remaining.
                if diff_seconds <= 0:
                    continue
                
                hours_remaining = diff_seconds / 3600.0
                if hours_remaining >= 24:
                    continue
                    
            except Exception:
                continue

            # Calculate distance
            dist = haversine_distance(target_lat, target_lon, lat, lon)
            
            if dist > 300:
                continue

            results.append(AuctionLotResponse(
                id=lot_id,
                title=title,
                description=desc,
                category_id=cat_id,
                condition=cond,
                location_id=loc_id,
                unit_count=units,
                auction_end_time=end_time_str,
                distance_miles=round(dist, 2)
            ))

        return results
