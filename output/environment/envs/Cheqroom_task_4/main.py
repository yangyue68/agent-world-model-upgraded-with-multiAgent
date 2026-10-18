import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class EquipmentCreateRequest(BaseModel):
    name: str
    category_id: int
    serial_number: str
    barcode: str
    condition: str
    location_id: int
    replacement_value: float
    availability_status: str
    maintenance_frequency: str


class CategoryResponse(BaseModel):
    id: int
    name: str


class LocationResponse(BaseModel):
    id: int
    name: str


class EquipmentCreateResponse(BaseModel):
    equipment_id: int
    maintenance_schedule_id: int


def get_next_maintenance_date(frequency: str) -> str:
    """Calculate the next maintenance date based on frequency."""
    today = datetime.now().date()
    
    if frequency == "Daily":
        next_date = today + timedelta(days=1)
    elif frequency == "Weekly":
        next_date = today + timedelta(weeks=1)
    elif frequency == "Monthly":
        # Approximate monthly as 30 days
        next_date = today + timedelta(days=30)
    elif frequency == "Quarterly":
        # Approximate quarterly as 90 days
        next_date = today + timedelta(days=90)
    elif frequency == "Semi-Annually":
        # Approximate semi-annually as 180 days
        next_date = today + timedelta(days=180)
    elif frequency == "Annually":
        # Approximate annually as 365 days
        next_date = today + timedelta(days=365)
    else:
        raise ValueError(f"Invalid frequency: {frequency}")
    
    return next_date.isoformat()


@app.post("/equipment", response_model=EquipmentCreateResponse, operation_id="create_equipment_record")
def create_equipment_record(request: EquipmentCreateRequest):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User ID not found in environment")
    
    try:
        created_by = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with engine.connect() as conn:
        # Validate category exists
        cat_result = conn.execute(
            text("SELECT id FROM categories WHERE id = :cat_id"),
            {"cat_id": request.category_id}
        ).fetchone()
        if not cat_result:
            raise HTTPException(status_code=400, detail=f"Category with ID {request.category_id} not found")

        # Validate location exists
        loc_result = conn.execute(
            text("SELECT id FROM locations WHERE id = :loc_id"),
            {"loc_id": request.location_id}
        ).fetchone()
        if not loc_result:
            raise HTTPException(status_code=400, detail=f"Location with ID {request.location_id} not found")

        # Check for duplicate serial number
        dup_serial = conn.execute(
            text("SELECT id FROM equipment WHERE serial_number = :sn"),
            {"sn": request.serial_number}
        ).fetchone()
        if dup_serial:
            raise HTTPException(status_code=400, detail="Serial number already exists")

        # Check for duplicate barcode
        dup_barcode = conn.execute(
            text("SELECT id FROM equipment WHERE barcode = :bc"),
            {"bc": request.barcode}
        ).fetchone()
        if dup_barcode:
            raise HTTPException(status_code=400, detail="Barcode already exists")

        # Insert equipment record
        now = datetime.now().isoformat(sep=' ', timespec='seconds')
        result = conn.execute(
            text("""
                INSERT INTO equipment (name, category_id, serial_number, barcode, condition, 
                                       location_id, replacement_value, availability_status, 
                                       created_by, created_at, updated_at)
                VALUES (:name, :category_id, :serial_number, :barcode, :condition,
                        :location_id, :replacement_value, :availability_status,
                        :created_by, :now, :now)
            """),
            {
                "name": request.name,
                "category_id": request.category_id,
                "serial_number": request.serial_number,
                "barcode": request.barcode,
                "condition": request.condition,
                "location_id": request.location_id,
                "replacement_value": request.replacement_value,
                "availability_status": request.availability_status,
                "created_by": created_by,
                "now": now
            }
        )
        equipment_id = result.lastrowid

        # Calculate next maintenance date
        next_maint_date = get_next_maintenance_date(request.maintenance_frequency)

        # Insert maintenance schedule
        maint_result = conn.execute(
            text("""
                INSERT INTO maintenance_schedules (equipment_id, frequency, last_maintenance_date,
                                                   next_maintenance_date, notes, created_by, 
                                                   created_at, updated_at)
                VALUES (:equipment_id, :frequency, NULL, :next_maintenance_date, NULL,
                        :created_by, :now, :now)
            """),
            {
                "equipment_id": equipment_id,
                "frequency": request.maintenance_frequency,
                "next_maintenance_date": next_maint_date,
                "created_by": created_by,
                "now": now
            }
        )
        maintenance_schedule_id = maint_result.lastrowid

        conn.commit()

    return EquipmentCreateResponse(
        equipment_id=equipment_id,
        maintenance_schedule_id=maintenance_schedule_id
    )


@app.get("/categories", response_model=List[CategoryResponse], operation_id="find_categories")
def find_categories(name: str = Query(..., description="Category name")):
    with engine.connect() as conn:
        results = conn.execute(
            text("SELECT id, name FROM categories WHERE name LIKE :pattern"),
            {"pattern": f"%{name}%"}
        ).fetchall()
    
    return [CategoryResponse(id=row.id, name=row.name) for row in results]


@app.get("/locations", response_model=List[LocationResponse], operation_id="find_locations")
def find_locations(name: str = Query(..., description="Location name")):
    with engine.connect() as conn:
        results = conn.execute(
            text("SELECT id, name FROM locations WHERE name LIKE :pattern"),
            {"pattern": f"%{name}%"}
        ).fetchall()
    
    return [LocationResponse(id=row.id, name=row.name) for row in results]
