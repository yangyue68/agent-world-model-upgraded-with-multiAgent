import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="AutoBridge Dealer API")


class CreateListingRequest(BaseModel):
    vehicle_vin: str = Field(..., description="VIN of the vehicle to list")
    location_name: str = Field(..., description="Name of the location where vehicle is located")
    price: float = Field(..., description="Sale price of the vehicle")
    mileage: int = Field(..., description="Odometer reading in miles")
    condition: str = Field(..., description="Vehicle condition - 'New', 'Used', or 'Certified Pre-Owned'")
    is_certified_pre_owned: bool = Field(..., description="Whether the vehicle is certified pre-owned")


class ListingResponse(BaseModel):
    id: int
    vehicle_id: int
    location_id: int
    price: float
    mileage: int
    condition: str
    is_certified_pre_owned: bool
    status: str
    listed_by_user_id: int
    created_at: str
    updated_at: str


def _to_iso(dt_val) -> str:
    if dt_val is None:
        return ""
    if isinstance(dt_val, str):
        return dt_val
    if isinstance(dt_val, datetime):
        return dt_val.isoformat()
    return str(dt_val)


@app.post("/listings", response_model=ListingResponse, operation_id="create_vehicle_listing")
def create_vehicle_listing(req: CreateListingRequest):
    # Validate condition against allowed values from schema CHECK constraint
    allowed_conditions = {"New", "Used", "Certified Pre-Owned"}
    if req.condition not in allowed_conditions:
        raise HTTPException(status_code=400, detail=f"Invalid condition. Must be one of {allowed_conditions}")

    # Get current user ID from environment variable
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=500, detail="Current user ID not available")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID format")

    with engine.connect() as conn:
        # Look up vehicle by VIN
        result = conn.execute(
            text("SELECT id FROM vehicles WHERE vin = :vin"),
            {"vin": req.vehicle_vin}
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Vehicle with VIN '{req.vehicle_vin}' not found")
        vehicle_id = row[0]

        # Look up location by name
        result = conn.execute(
            text("SELECT id FROM locations WHERE name = :name"),
            {"name": req.location_name}
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Location '{req.location_name}' not found")
        location_id = row[0]

        # Verify user exists
        result = conn.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": current_user_id}
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Current user not found")

        # Determine is_certified_pre_owned integer value
        is_cpo_int = 1 if req.is_certified_pre_owned else 0

        # Insert new listing
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text("""
                INSERT INTO listings (vehicle_id, location_id, price, mileage, condition, 
                                      is_certified_pre_owned, status, listed_by_user_id, 
                                      created_at, updated_at)
                VALUES (:vehicle_id, :location_id, :price, :mileage, :condition,
                        :is_cpo, :status, :listed_by_user_id, :created_at, :updated_at)
            """),
            {
                "vehicle_id": vehicle_id,
                "location_id": location_id,
                "price": req.price,
                "mileage": req.mileage,
                "condition": req.condition,
                "is_cpo": is_cpo_int,
                "status": "Available",
                "listed_by_user_id": current_user_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        conn.commit()
        listing_id = result.lastrowid

        # Fetch the created listing to return exact stored values
        result = conn.execute(
            text("""
                SELECT id, vehicle_id, location_id, price, mileage, condition,
                       is_certified_pre_owned, status, listed_by_user_id,
                       created_at, updated_at
                FROM listings WHERE id = :lid
            """),
            {"lid": listing_id}
        )
        row = result.fetchone()

    return ListingResponse(
        id=row[0],
        vehicle_id=row[1],
        location_id=row[2],
        price=float(row[3]),
        mileage=int(row[4]),
        condition=row[5],
        is_certified_pre_owned=bool(row[6]),
        status=row[7],
        listed_by_user_id=int(row[8]),
        created_at=_to_iso(row[9]),
        updated_at=_to_iso(row[10]),
    )
