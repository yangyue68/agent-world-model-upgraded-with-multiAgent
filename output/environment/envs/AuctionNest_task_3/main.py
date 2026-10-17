import os
from typing import Optional, List
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class LocationModel(BaseModel):
    id: int
    name: str
    address: Optional[str] = None
    city: str
    state: str
    postal_code: Optional[str] = None
    country: str
    latitude: float
    longitude: float


class AuctionModel(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    location_id: int
    start_time: str
    end_time: str
    starting_price: float
    current_price: float
    status: str
    created_by: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    location: LocationModel


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points on Earth in miles."""
    import math

    R = 3958.8  # Earth radius in miles

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


@app.get("/auctions/nearby", response_model=List[AuctionModel], operation_id="find_auctions_nearby_new_york")
def find_auctions_nearby_new_york(
    city: str = Query(..., description="City name to search near"),
    state: str = Query(..., description="State abbreviation to search near"),
    radius_miles: int = Query(50, description="Search radius in miles (default 50)")
):
    """Find auctions within a specified radius of a given city/state."""
    
    # First, get the coordinates for the reference city/state
    with engine.connect() as conn:
        ref_query = text("""
            SELECT latitude, longitude 
            FROM locations 
            WHERE city = :city AND state = :state
            LIMIT 1
        """)
        ref_result = conn.execute(ref_query, {"city": city, "state": state}).fetchone()
        
        if ref_result is None:
            raise HTTPException(status_code=404, detail=f"Location {city}, {state} not found")
        
        ref_lat, ref_lon = ref_result[0], ref_result[1]
        
        # Get all auctions with their locations
        auction_query = text("""
            SELECT 
                a.id,
                a.title,
                a.description,
                a.location_id,
                a.start_time,
                a.end_time,
                a.starting_price,
                a.current_price,
                a.status,
                a.created_by,
                a.created_at,
                a.updated_at,
                l.id as loc_id,
                l.name as loc_name,
                l.address as loc_address,
                l.city as loc_city,
                l.state as loc_state,
                l.postal_code as loc_postal_code,
                l.country as loc_country,
                l.latitude as loc_latitude,
                l.longitude as loc_longitude
            FROM auctions a
            JOIN locations l ON a.location_id = l.id
            ORDER BY a.start_time DESC
        """)
        
        results = conn.execute(auction_query).fetchall()
        
        nearby_auctions = []
        for row in results:
            loc_lat = row.loc_latitude
            loc_lon = row.loc_longitude
            
            distance = haversine_distance(ref_lat, ref_lon, loc_lat, loc_lon)
            
            if distance <= radius_miles:
                auction_data = {
                    "id": row.id,
                    "title": row.title,
                    "description": row.description,
                    "location_id": row.location_id,
                    "start_time": str(row.start_time) if row.start_time else None,
                    "end_time": str(row.end_time) if row.end_time else None,
                    "starting_price": row.starting_price,
                    "current_price": row.current_price,
                    "status": row.status,
                    "created_by": row.created_by,
                    "created_at": str(row.created_at) if row.created_at else None,
                    "updated_at": str(row.updated_at) if row.updated_at else None,
                    "location": {
                        "id": row.loc_id,
                        "name": row.loc_name,
                        "address": row.loc_address,
                        "city": row.loc_city,
                        "state": row.loc_state,
                        "postal_code": row.loc_postal_code,
                        "country": row.loc_country,
                        "latitude": row.loc_latitude,
                        "longitude": row.loc_longitude,
                    }
                }
                nearby_auctions.append(auction_data)
        
        return nearby_auctions
