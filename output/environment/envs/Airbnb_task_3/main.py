import os
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PropertySearchResult(BaseModel):
    id: int
    title: str
    description: Optional[str]
    property_type: str
    max_guests: int
    bedrooms: int
    bathrooms: float
    price_per_night: float
    instant_book: bool
    amenities: List[str]


@app.get("/properties/search", operation_id="search_vacation_rentals")
def search_vacation_rentals(
    location: str = Query(..., description="Location to search for rentals (e.g., 'Sedona, Arizona')"),
    check_in: str = Query(..., description="Check-in date in YYYY-MM-DD format"),
    check_out: str = Query(..., description="Check-out date in YYYY-MM-DD format"),
    guests: int = Query(..., description="Number of guests"),
):
    # Parse location into city and state
    parts = [p.strip() for p in location.split(",")]
    if len(parts) >= 2:
        city = parts[0]
        state = parts[1]
    else:
        city = parts[0]
        state = None

    with engine.connect() as conn:
        # Find matching location IDs
        if state:
            loc_query = text("""
                SELECT id FROM locations 
                WHERE city = :city AND state = :state
            """)
            loc_result = conn.execute(loc_query, {"city": city, "state": state}).fetchall()
        else:
            loc_query = text("""
                SELECT id FROM locations 
                WHERE city = :city
            """)
            loc_result = conn.execute(loc_query, {"city": city}).fetchall()

        location_ids = [row[0] for row in loc_result]

        if not location_ids:
            return []

        placeholders = ",".join([f":loc_{i}" for i in range(len(location_ids))])
        params = {f"loc_{i}": lid for i, lid in enumerate(location_ids)}
        params["guests"] = guests

        # Get properties that match location, guest capacity, instant booking, and have required amenities
        query = text(f"""
            SELECT DISTINCT p.id, p.title, p.description, p.property_type, p.max_guests, 
                   p.bedrooms, p.bathrooms, p.price_per_night, p.instant_book
            FROM properties p
            JOIN property_amenities pa1 ON p.id = pa1.property_id
            JOIN amenities a1 ON pa1.amenity_id = a1.id
            JOIN property_amenities pa2 ON p.id = pa2.property_id
            JOIN amenities a2 ON pa2.amenity_id = a2.id
            WHERE p.location_id IN ({placeholders})
              AND p.max_guests >= :guests
              AND p.instant_book = 1
              AND a1.name = 'Free Parking'
              AND a2.name = 'Air Conditioning'
        """)

        results = conn.execute(query, params).fetchall()

        if not results:
            return []

        # Fetch amenities for each result
        property_ids = [r[0] for r in results]
        ph_placeholders = ",".join([f":pid_{i}" for i in range(len(property_ids))])
        ph_params = {f"pid_{i}": pid for i, pid in enumerate(property_ids)}

        amenities_query = text(f"""
            SELECT pa.property_id, a.name
            FROM property_amenities pa
            JOIN amenities a ON pa.amenity_id = a.id
            WHERE pa.property_id IN ({ph_placeholders})
        """)

        amenities_rows = conn.execute(amenities_query, ph_params).fetchall()

        prop_amenities_map = {}
        for row in amenities_rows:
            pid = row[0]
            name = row[1]
            if pid not in prop_amenities_map:
                prop_amenities_map[pid] = []
            prop_amenities_map[pid].append(name)

        output = []
        for r in results:
            pid = r[0]
            amenities_list = prop_amenities_map.get(pid, [])
            output.append(PropertySearchResult(
                id=r[0],
                title=r[1],
                description=r[2],
                property_type=r[3],
                max_guests=r[4],
                bedrooms=r[5],
                bathrooms=r[6],
                price_per_night=r[7],
                instant_book=bool(r[8]),
                amenities=amenities_list
            ))

        return output
