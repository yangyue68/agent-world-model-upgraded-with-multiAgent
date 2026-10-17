import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# Initialize FastAPI app
app = FastAPI(title="AssetPulse API")

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)


class DepartmentResponse(BaseModel):
    id: int
    name: str
    facility_name: str
    floor: Optional[str] = None


class AssetCreateRequest(BaseModel):
    name: str
    manufacturer: str
    model: str
    serial_number: str
    department_id: int
    calibration_date: str  # YYYY-MM-DD
    warranty_expiry: str   # YYYY-MM-DD
    qr_tag_id: str


class AssetResponse(BaseModel):
    id: int
    name: str
    manufacturer: str
    model: str
    serial_number: str
    department_id: int
    calibration_date: str
    warranty_expiry: str
    status: str
    qr_tag_id: str


@app.get("/departments", response_model=DepartmentResponse, operation_id="find_department_by_name_and_facility")
def find_department_by_name_and_facility(
    name: str = Query(..., description="Name of the department"),
    facility_name: str = Query(..., description="Name of the facility where the department is located")
):
    """
    Retrieve the department ID given the department name and facility name.
    """
    query = text("""
        SELECT id, name, facility_name, floor 
        FROM departments 
        WHERE name = :name AND facility_name = :facility_name
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"name": name, "facility_name": facility_name}).fetchone()
        
    if not result:
        raise HTTPException(status_code=404, detail="Department not found")
    
    return DepartmentResponse(
        id=result.id,
        name=result.name,
        facility_name=result.facility_name,
        floor=result.floor
    )


@app.post("/assets", response_model=AssetResponse, operation_id="create_asset")
def create_asset(asset_data: AssetCreateRequest):
    """
    Create a new asset record and attach a QR tag.
    """
    # Validate department exists
    dept_query = text("SELECT id FROM departments WHERE id = :dept_id")
    with engine.connect() as conn:
        dept_result = conn.execute(dept_query, {"dept_id": asset_data.department_id}).fetchone()
        if not dept_result:
            raise HTTPException(status_code=404, detail="Department not found")

        # Check if serial number already exists
        serial_check = text("SELECT id FROM assets WHERE serial_number = :serial")
        existing_serial = conn.execute(serial_check, {"serial": asset_data.serial_number}).fetchone()
        if existing_serial:
            raise HTTPException(status_code=409, detail="Serial number already exists")

        # Check if QR tag ID already exists
        qr_check = text("SELECT id FROM qr_tags WHERE tag_id = :tag_id")
        existing_qr = conn.execute(qr_check, {"tag_id": asset_data.qr_tag_id}).fetchone()
        if existing_qr:
            raise HTTPException(status_code=409, detail="QR Tag ID already exists")

        # Insert Asset
        insert_asset_query = text("""
            INSERT INTO assets (
                name, manufacturer, model, serial_number, department_id, 
                calibration_date, warranty_expiry, status, created_at, updated_at
            ) VALUES (
                :name, :manufacturer, :model, :serial_number, :department_id,
                :calibration_date, :warranty_expiry, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """)
        
        params = {
            "name": asset_data.name,
            "manufacturer": asset_data.manufacturer,
            "model": asset_data.model,
            "serial_number": asset_data.serial_number,
            "department_id": asset_data.department_id,
            "calibration_date": asset_data.calibration_date,
            "warranty_expiry": asset_data.warranty_expiry
        }
        
        result = conn.execute(insert_asset_query, params)
        asset_id = result.lastrowid
        
        # Insert QR Tag
        insert_qr_query = text("""
            INSERT INTO qr_tags (
                tag_id, asset_id, assigned_at, created_at, updated_at
            ) VALUES (
                :tag_id, :asset_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """)
        
        conn.execute(insert_qr_query, {
            "tag_id": asset_data.qr_tag_id,
            "asset_id": asset_id
        })
        
        conn.commit()

    # Fetch the created asset to return full details including generated fields
    fetch_asset_query = text("""
        SELECT a.id, a.name, a.manufacturer, a.model, a.serial_number, a.department_id,
               a.calibration_date, a.warranty_expiry, a.status, q.tag_id as qr_tag_id
        FROM assets a
        JOIN qr_tags q ON a.id = q.asset_id
        WHERE a.id = :asset_id
    """)
    
    with engine.connect() as conn:
        final_result = conn.execute(fetch_asset_query, {"asset_id": asset_id}).fetchone()
        
    if not final_result:
        raise HTTPException(status_code=500, detail="Failed to retrieve created asset")

    return AssetResponse(
        id=final_result.id,
        name=final_result.name,
        manufacturer=final_result.manufacturer,
        model=final_result.model,
        serial_number=final_result.serial_number,
        department_id=final_result.department_id,
        calibration_date=str(final_result.calibration_date),
        warranty_expiry=str(final_result.warranty_expiry),
        status=final_result.status,
        qr_tag_id=final_result.qr_tag_id
    )
