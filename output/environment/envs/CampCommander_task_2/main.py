import os
from typing import Optional, List

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CampsiteResponse(BaseModel):
    campsite_id: int
    campground_id: int
    campground_name: str
    site_number: str
    max_rv_length: Optional[int]
    max_occupancy: Optional[int]
    has_electric_30_amp: bool
    has_electric_50_amp: bool
    has_water: bool
    has_sewer: bool
    has_full_hookups: bool
    base_price_per_night: float
    description: Optional[str]
    address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip: Optional[str]


@app.get("/available-campsites", response_model=List[CampsiteResponse], operation_id="find_available_campsites")
def find_available_campsites(
    check_in_date: str = Query(..., description="Check-in date in YYYY-MM-DD format"),
    check_out_date: str = Query(..., description="Check-out date in YYYY-MM-DD format"),
    max_rv_length: int = Query(..., description="Maximum RV length in feet (e.g., 40)"),
    max_occupancy: int = Query(..., description="Maximum occupancy (e.g., 5)"),
    has_electric_30_amp: Optional[bool] = Query(None, description="Filter for sites with 30 amp electric hookups"),
    has_electric_50_amp: Optional[bool] = Query(None, description="Filter for sites with 50 amp electric hookups"),
    has_water: Optional[bool] = Query(None, description="Filter for sites with water hookups"),
    has_sewer: Optional[bool] = Query(None, description="Filter for sites with sewer hookups"),
    has_full_hookups: Optional[bool] = Query(None, description="Filter for sites with full hookups (electric, water, sewer)")
):
    """
    Search for campsites that can accommodate a specific RV size and party size 
    within the given dates, optionally filtering by amenities/hookups.
    
    A campsite is considered available if there are no overlapping confirmed/pending reservations
    for the requested dates.
    """
    
    # Base query joining campsites with campgrounds
    sql_query = """
        SELECT 
            c.id as campsite_id,
            c.campground_id,
            cg.name as campground_name,
            c.site_number,
            c.max_rv_length,
            c.max_occupancy,
            c.has_electric_30_amp,
            c.has_electric_50_amp,
            c.has_water,
            c.has_sewer,
            c.has_full_hookups,
            c.base_price_per_night,
            c.description,
            cg.address,
            cg.city,
            cg.state,
            cg.zip
        FROM campsites c
        JOIN campgrounds cg ON c.campground_id = cg.id
        WHERE c.is_active = 1
          AND (c.max_rv_length IS NULL OR c.max_rv_length >= :max_rv_length_req)
          AND (c.max_occupancy IS NULL OR c.max_occupancy >= :max_occupancy_req)
          -- Ensure the campsite is NOT booked during the requested period
          AND NOT EXISTS (
              SELECT 1 
              FROM reservations r 
              WHERE r.campsite_id = c.id 
                AND r.status IN ('confirmed', 'pending')
                AND r.check_in_date < :check_out_date_param
                AND r.check_out_date > :check_in_date_param
          )
    """
    
    params = {
        "max_rv_length_req": max_rv_length,
        "max_occupancy_req": max_occupancy,
        "check_in_date_param": check_in_date,
        "check_out_date_param": check_out_date
    }
    
    # Apply optional boolean filters for hookups/amenities
    # Note: In DB, these are stored as integers 0 or 1.
    # If the user requests True, we filter for 1.
    # If the user requests False, we typically don't filter unless explicitly asked to exclude them?
    # The spec says "Filter for sites with...", implying inclusion. 
    # Usually, search APIs treat missing parameters as "don't care".
    # If a parameter is present and true, we require it.
    # If a parameter is present and false, does it mean "must not have"? 
    # Given the context of finding suitable sites, usually you only filter when looking FOR features.
    # However, to be safe and follow standard REST patterns where query param presence implies constraint:
    # Let's assume if passed as True, we require it. If passed as False, we might ignore it or require absence.
    # Looking at typical booking engines, you rarely search for "no electricity". You search for "with electricity".
    # I will implement logic such that if the param is provided and is True, we enforce it.
    # If provided and False, we will also enforce it (i.e., must NOT have), to respect explicit filtering intent.
    
    def add_bool_filter(field_name: str, value: Optional[bool]):
        nonlocal sql_query, params
        if value is None:
            return
        
        # Convert Python bool to SQL integer representation used in DB (0 or 1)
        db_val = 1 if value else 0
        
        condition = f"AND c.{field_name} = :{field_name}_val"
        sql_query += "\n          " + condition
        params[f"{field_name}_val"] = db_val

    add_bool_filter("has_electric_30_amp", has_electric_30_amp)
    add_bool_filter("has_electric_50_amp", has_electric_50_amp)
    add_bool_filter("has_water", has_water)
    add_bool_filter("has_sewer", has_sewer)
    add_bool_filter("has_full_hookups", has_full_hookups)

    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql_query), params)
            rows = result.fetchall()
            
            output = []
            for row in rows:
                # Map SQLAlchemy Row to Pydantic Model
                # Handle potential None values from DB for booleans/integers carefully
                item = CampsiteResponse(
                    campsite_id=row.campsite_id,
                    campground_id=row.campground_id,
                    campground_name=row.campground_name,
                    site_number=row.site_number,
                    max_rv_length=row.max_rv_length,
                    max_occupancy=row.max_occupancy,
                    has_electric_30_amp=bool(row.has_electric_30_amp),
                    has_electric_50_amp=bool(row.has_electric_50_amp),
                    has_water=bool(row.has_water),
                    has_sewer=bool(row.has_sewer),
                    has_full_hookups=bool(row.has_full_hookups),
                    base_price_per_night=float(row.base_price_per_night) if row.base_price_per_night is not None else 0.0,
                    description=row.description,
                    address=row.address,
                    city=row.city,
                    state=row.state,
                    zip=row.zip
                )
                output.append(item)
                
            return output
            
    except Exception as e:
        # In a real app, proper logging would occur here
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
