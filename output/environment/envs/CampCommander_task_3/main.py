import os
from typing import List

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class AccommodationResponse(BaseModel):
    id: int
    name: str
    type_name: str
    bedrooms: int
    max_occupancy: int
    has_air_conditioning: bool
    has_kitchenette: bool
    nightly_rate: float


@app.get("/available-accommodations", response_model=List[AccommodationResponse], operation_id="find_available_accommodations")
def find_available_accommodations(
    check_in_date: str = Query(..., description="Check-in date in YYYY-MM-DD format"),
    check_out_date: str = Query(..., description="Check-out date in YYYY-MM-DD format"),
    guests: int = Query(..., description="Number of guests"),
):
    """
    Search for accommodations that match the criteria: 
    - Type is Cabin or Yurt
    - 2 bedrooms
    - Has air conditioning
    - Has kitchenette
    - Max occupancy >= guests
    - Available for the given date range (no overlapping confirmed/pending bookings)
    
    Returns the nightly rate for each matching accommodation.
    """
    
    # Validate dates roughly to ensure they are strings in expected format if needed, 
    # but primarily rely on SQL comparison which works well with ISO format strings in SQLite.
    
    query_sql = text("""
        SELECT 
            a.id,
            a.name,
            at.name as type_name,
            a.bedrooms,
            a.max_occupancy,
            a.has_air_conditioning,
            a.has_kitchenette,
            a.nightly_rate
        FROM accommodations a
        JOIN accommodation_types at ON a.type_id = at.id
        WHERE 
            at.name IN ('Cabin', 'Yurt')
            AND a.bedrooms = 2
            AND a.has_air_conditioning = 1
            AND a.has_kitchenette = 1
            AND a.max_occupancy >= :guests
            AND NOT EXISTS (
                SELECT 1 
                FROM bookings b 
                WHERE b.accommodation_id = a.id 
                  AND b.status IN ('confirmed', 'pending')
                  AND b.check_in_date < :check_out_date 
                  AND b.check_out_date > :check_in_date
            )
    """)

    params = {
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "guests": guests
    }

    try:
        with SessionLocal() as db:
            result = db.execute(query_sql, params).fetchall()
            
            accommodations = []
            for row in result:
                accommodations.append(AccommodationResponse(
                    id=row.id,
                    name=row.name,
                    type_name=row.type_name,
                    bedrooms=row.bedrooms,
                    max_occupancy=row.max_occupancy,
                    has_air_conditioning=bool(row.has_air_conditioning),
                    has_kitchenette=bool(row.has_kitchenette),
                    nightly_rate=float(row.nightly_rate)
                ))
            
            return accommodations
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
