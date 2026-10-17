import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UnitResponse(BaseModel):
    unit_number: str
    floor_plan: str
    square_footage: int
    rent_amount: float
    availability_date: str
    amenities: List[str]


@app.get(
    "/properties/{property_id}/units",
    response_model=List[UnitResponse],
    operation_id="find_available_units",
    tags=["PrimaryResource"],
)
def find_available_units(
    property_id: int,
    bedrooms: int = Query(2, description="Number of bedrooms (default: 2)"),
    min_bathrooms: float = Query(1.0, description="Minimum number of bathrooms (default: 1)"),
    max_rent: float = Query(2200.0, description="Maximum monthly rent amount (default: 2200)"),
):
    """
    Search for all residential units in a specified property that are currently vacant or available soon,
    have the specified number of bedrooms, at least the minimum number of bathrooms, and a monthly rent
    no higher than the maximum rent. Returns unit number, floor plan, square footage, rent amount,
    availability date, and amenities, sorted by availability date ascending.
    """
    with engine.connect() as conn:
        # First verify the property exists and is residential
        prop_result = conn.execute(
            text("SELECT id, name, property_type FROM properties WHERE id = :pid"),
            {"pid": property_id},
        ).fetchone()

        if not prop_result:
            raise HTTPException(status_code=404, detail="Property not found")

        if prop_result.property_type != "residential":
            raise HTTPException(status_code=400, detail="Property is not residential")

        # Query units matching criteria
        query = text("""
            SELECT 
                u.id AS unit_id,
                u.unit_number,
                fp.name AS floor_plan,
                fp.square_footage,
                u.rent_amount,
                u.availability_date
            FROM units u
            JOIN floor_plans fp ON u.floor_plan_id = fp.id
            WHERE u.property_id = :property_id
              AND u.status IN ('vacant', 'available_soon')
              AND fp.bedrooms = :bedrooms
              AND fp.bathrooms >= :min_bathrooms
              AND u.rent_amount <= :max_rent
            ORDER BY u.availability_date ASC
        """)

        results = conn.execute(
            query,
            {
                "property_id": property_id,
                "bedrooms": bedrooms,
                "min_bathrooms": min_bathrooms,
                "max_rent": max_rent,
            },
        ).fetchall()

        # For each unit, fetch its amenities
        unit_responses = []
        for row in results:
            amenity_query = text("""
                SELECT a.name
                FROM unit_amenities ua
                JOIN amenities a ON ua.amenity_id = a.id
                WHERE ua.unit_id = :unit_id
                ORDER BY a.name
            """)
            amenity_rows = conn.execute(
                amenity_query, {"unit_id": row.unit_id}
            ).fetchall()
            amenities = [r.name for r in amenity_rows]

            unit_responses.append(
                UnitResponse(
                    unit_number=row.unit_number,
                    floor_plan=row.floor_plan,
                    square_footage=row.square_footage if row.square_footage else 0,
                    rent_amount=float(row.rent_amount),
                    availability_date=str(row.availability_date) if row.availability_date else "",
                    amenities=amenities,
                )
            )

        return unit_responses
