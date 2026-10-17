import os
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CampsiteResponse(BaseModel):
    id: int
    name: str
    site_type: str
    max_occupancy: int
    base_price_per_night: float
    description: str


@app.get("/available-campsites", response_model=List[CampsiteResponse], operation_id="find_available_tent_campsites")
def find_available_tent_campsites(
    check_in_date: str = Query(..., description="Check-in date in YYYY-MM-DD format"),
    check_out_date: str = Query(..., description="Check-out date in YYYY-MM-DD format"),
    adults: int = Query(..., description="Number of adults (must be 2)"),
    amenity_ids: Optional[List[int]] = Query(None, description="List of amenity IDs that must be present on the campsites"),
    amenity_names: Optional[List[str]] = Query(None, description="List of amenity names that must be present on the campsites"),
):
    """
    Search for tent campsites that are available for the specified dates and have required amenities.
    Returns campsites that can accommodate the specified number of adults.
    """
    
    # Build dynamic query parts
    params = {
        "check_in": check_in_date,
        "check_out": check_out_date,
        "adults": adults,
    }

    # Base query selecting distinct campsites
    sql_query = """
        SELECT DISTINCT c.id, c.name, c.site_type, c.max_occupancy, c.base_price_per_night, c.description
        FROM campsites c
        WHERE c.is_active = 1
          AND c.site_type = 'tent'
          AND c.max_occupancy >= :adults
    """

    # Filter by Amenities if provided
    target_amenity_ids = []
    
    if amenity_ids:
        target_amenity_ids.extend(amenity_ids)
        
    if amenity_names:
        # We need to look up IDs for these names
        # To keep it simple and efficient within one request context, 
        # we can join or subquery. However, since we might mix IDs and Names,
        # let's resolve names to IDs first using a separate small query or handle in logic.
        # Given the constraint of "parameterized SQL" and efficiency, resolving names via DB is safer than assuming static mapping.
        with engine.connect() as conn:
            placeholders = ", ".join([f":name_{i}" for i in range(len(amenity_names))])
            lookup_sql = f"SELECT id FROM amenities WHERE name IN ({placeholders})"
            lookup_params = {f"name_{i}": val for i, val in enumerate(amenity_names)}
            result = conn.execute(text(lookup_sql), lookup_params)
            found_ids = [row[0] for row in result.fetchall()]
            target_amenity_ids.extend(found_ids)

    # Remove duplicates just in case
    unique_target_amenity_ids = list(set(target_amenity_ids))

    if unique_target_amenity_ids:
        # Add condition: Campsite must have ALL requested amenities
        # This requires counting matches per campsite equals count of requested amenities
        
        amenity_placeholders = ", ".join([f":aid_{i}" for i in range(len(unique_target_amenity_ids))])
        for i, aid in enumerate(unique_target_amenity_ids):
            params[f"aid_{i}"] = aid
            
        # Subquery to ensure all amenities exist for the campsite
        sql_query += f"""
          AND c.id IN (
              SELECT ca.campsite_id
              FROM campsite_amenities ca
              WHERE ca.amenity_id IN ({amenity_placeholders})
              GROUP BY ca.campsite_id
              HAVING COUNT(DISTINCT ca.amenity_id) = {len(unique_target_amenity_ids)}
          )
        """

    # Exclude campsites with overlapping confirmed/pending reservations
    # Overlap condition: existing.check_in < new.check_out AND existing.check_out > new.check_in
    sql_query += """
      AND c.id NOT IN (
          SELECT r.campsite_id
          FROM reservations r
          WHERE r.status IN ('confirmed', 'pending')
            AND r.check_in_date < :check_out
            AND r.check_out_date > :check_in
      )
    """

    final_sql = text(sql_query)

    results = []
    with engine.connect() as conn:
        rows = conn.execute(final_sql, params).fetchall()
        for row in rows:
            results.append(CampsiteResponse(
                id=row[0],
                name=row[1],
                site_type=row[2],
                max_occupancy=row[3],
                base_price_per_night=row[4],
                description=row[5]
            ))

    return results
