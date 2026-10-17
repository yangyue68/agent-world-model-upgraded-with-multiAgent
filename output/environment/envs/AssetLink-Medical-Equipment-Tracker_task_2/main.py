import os
from datetime import date, datetime
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="AssetLink Medical Equipment Tracker API")


class ComplianceReportItem(BaseModel):
    equipment_id: int
    asset_tag: str
    name: str
    serial_number: Optional[str]
    manufacturer: Optional[str]
    model: Optional[str]
    warranty_expiration_date: str
    assignment_id: int
    location_name: str
    assigned_at: str


class EquipmentCategoryItem(BaseModel):
    id: int
    name: str


class LocationItem(BaseModel):
    id: int
    name: str


@app.get(
    "/compliance-report/expired-warranties-mobile-carts",
    response_model=List[ComplianceReportItem],
    operation_id="generate_expired_warranty_compliance_report",
    summary="Generate compliance report of portable X-ray units with expired warranties assigned to Radiology Mobile Carts",
    description="Returns a list of portable X-ray units with warranty expiration dates before today that are currently assigned to 'Radiology Mobile Carts'. Includes equipment details and assignment information.",
    tags=["PrimaryResource"],
)
def generate_expired_warranty_compliance_report():
    today = date.today().isoformat()
    
    query = text("""
        SELECT 
            e.id AS equipment_id,
            e.asset_tag,
            e.name,
            e.serial_number,
            e.manufacturer,
            e.model,
            e.warranty_expiration_date,
            a.id AS assignment_id,
            l.name AS location_name,
            a.assigned_at
        FROM equipment e
        JOIN equipment_categories ec ON e.category_id = ec.id
        JOIN assignments a ON e.id = a.equipment_id
        JOIN locations l ON a.location_id = l.id
        WHERE ec.name = 'Portable X-Ray Units'
          AND l.name = 'Radiology Mobile Carts'
          AND a.unassigned_at IS NULL
          AND e.warranty_expiration_date < :today
        ORDER BY e.id
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"today": today})
        rows = result.fetchall()
    
    items = []
    for row in rows:
        items.append(ComplianceReportItem(
            equipment_id=row.equipment_id,
            asset_tag=row.asset_tag,
            name=row.name,
            serial_number=row.serial_number,
            manufacturer=row.manufacturer,
            model=row.model,
            warranty_expiration_date=str(row.warranty_expiration_date),
            assignment_id=row.assignment_id,
            location_name=row.location_name,
            assigned_at=str(row.assigned_at),
        ))
    
    return items


@app.get(
    "/equipment-categories",
    response_model=List[EquipmentCategoryItem],
    operation_id="find_equipment_categories",
    summary="Find equipment categories by name",
    description="Retrieve equipment category IDs by their names for use in filtering equipment.",
    tags=["PrimaryResource"],
)
def find_equipment_categories(name: str = Query(..., description="Name of the equipment category")):
    query = text("""
        SELECT id, name
        FROM equipment_categories
        WHERE name LIKE :name_pattern
        ORDER BY id
    """)
    
    pattern = f"%{name}%"
    
    with engine.connect() as conn:
        result = conn.execute(query, {"name_pattern": pattern})
        rows = result.fetchall()
    
    return [EquipmentCategoryItem(id=row.id, name=row.name) for row in rows]


@app.get(
    "/locations",
    response_model=List[LocationItem],
    operation_id="find_locations",
    summary="Find locations by name",
    description="Retrieve location IDs by their names for use in filtering assignments.",
    tags=["PrimaryResource"],
)
def find_locations(name: str = Query(..., description="Name of the location")):
    query = text("""
        SELECT id, name
        FROM locations
        WHERE name LIKE :name_pattern
        ORDER BY id
    """)
    
    pattern = f"%{name}%"
    
    with engine.connect() as conn:
        result = conn.execute(query, {"name_pattern": pattern})
        rows = result.fetchall()
    
    return [LocationItem(id=row.id, name=row.name) for row in rows]
