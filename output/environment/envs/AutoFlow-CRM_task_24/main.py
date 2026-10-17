import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class VehicleResponse(BaseModel):
    id: int
    make: str
    model: str
    price: float


class UpdateVehicleResponse(BaseModel):
    id: int
    price: float
    status: str


@app.get("/vehicles", operation_id="find_vehicles", response_model=List[VehicleResponse])
def find_vehicles(
    make: str = Query(..., description="Vehicle make"),
    model: str = Query(..., description="Vehicle model"),
    max_price: float = Query(..., description="Maximum vehicle price"),
):
    """Filter vehicles by make, model and price under max_price."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT id, make, model, price FROM vehicles "
                "WHERE make = :make AND model = :model AND price < :max_price"
            ),
            {"make": make, "model": model, "max_price": max_price},
        )
        rows = result.fetchall()

    return [
        VehicleResponse(id=row[0], make=row[1], model=row[2], price=row[3])
        for row in rows
    ]


@app.put("/vehicles/{id}", operation_id="update_vehicle", response_model=UpdateVehicleResponse)
def update_vehicle(id: int):
    """Update the price of a vehicle to $24,500 and set its status to 'Available'."""
    new_price = 24500.0
    new_status = "Available"

    with engine.connect() as conn:
        # Check if vehicle exists
        existing = conn.execute(
            text("SELECT id FROM vehicles WHERE id = :id"),
            {"id": id},
        ).fetchone()

        if existing is None:
            raise HTTPException(status_code=404, detail="Vehicle not found")

        # Update the vehicle
        conn.execute(
            text(
                "UPDATE vehicles SET price = :price, status = :status, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = :id"
            ),
            {"price": new_price, "status": new_status, "id": id},
        )
        conn.commit()

        # Fetch updated record
        result = conn.execute(
            text("SELECT id, price, status FROM vehicles WHERE id = :id"),
            {"id": id},
        ).fetchone()

    if result is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    return UpdateVehicleResponse(id=result[0], price=result[1], status=result[2])
