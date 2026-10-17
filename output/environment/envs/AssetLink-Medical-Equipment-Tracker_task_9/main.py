import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="AssetLink Medical Equipment Tracker")


class ClinicianSearchResponse(BaseModel):
    id: int
    full_name: str


class UpdateEquipmentLocationResponse(BaseModel):
    message: str
    equipment_id: int
    checkout_id: int


def parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO-8601 timestamp, handling both T...Z and space-separated forms."""
    ts_str = ts_str.strip()
    # Handle Z suffix
    if ts_str.endswith('Z'):
        ts_str = ts_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {ts_str}")


@app.get("/clinicians/search", response_model=ClinicianSearchResponse, operation_id="search_clinician_by_name")
def search_clinician_by_name(name: str = Query(..., description="Full name of the clinician to search for")):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, full_name FROM clinicians WHERE full_name = :name"),
            {"name": name}
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Clinician not found")
        return ClinicianSearchResponse(id=result[0], full_name=result[1])


@app.put("/equipment/{asset_tag}/location", response_model=UpdateEquipmentLocationResponse, operation_id="update_equipment_location_and_status")
def update_equipment_location_and_status(
    asset_tag: str,
    clinician_id: int = Query(..., description="ID of the clinician associated with the equipment"),
    new_location_name: str = Query(..., description="Name of the new location for the equipment"),
    checkout_timestamp: str = Query(..., description="Timestamp when the equipment was checked out")
):
    parsed_ts = parse_timestamp(checkout_timestamp)
    
    with engine.begin() as conn:
        # Find equipment by asset_tag
        equip_result = conn.execute(
            text("SELECT id, current_location_id FROM equipment WHERE asset_tag = :asset_tag"),
            {"asset_tag": asset_tag}
        ).fetchone()
        if not equip_result:
            raise HTTPException(status_code=404, detail="Equipment not found")
        
        equipment_id = equip_result[0]
        
        # Find location by name
        loc_result = conn.execute(
            text("SELECT id FROM locations WHERE name = :loc_name"),
            {"loc_name": new_location_name}
        ).fetchone()
        if not loc_result:
            raise HTTPException(status_code=404, detail="Location not found")
        
        location_id = loc_result[0]
        
        # Verify clinician exists
        clin_result = conn.execute(
            text("SELECT id FROM clinicians WHERE id = :cid"),
            {"cid": clinician_id}
        ).fetchone()
        if not clin_result:
            raise HTTPException(status_code=404, detail="Clinician not found")
        
        # Update equipment status and location
        conn.execute(
            text("""
                UPDATE equipment 
                SET status = 'In Use', 
                    current_location_id = :loc_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :equip_id
            """),
            {"loc_id": location_id, "equip_id": equipment_id}
        )
        
        # Create checkout record
        checkout_result = conn.execute(
            text("""
                INSERT INTO equipment_checkouts (equipment_id, clinician_id, location_id, status, checked_out_at, created_at, updated_at)
                VALUES (:equip_id, :clin_id, :loc_id, 'In Use', :checked_out_at, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "equip_id": equipment_id,
                "clin_id": clinician_id,
                "loc_id": location_id,
                "checked_out_at": parsed_ts.isoformat(sep=' ')
            }
        )
        
        checkout_id = checkout_result.lastrowid
        
        return UpdateEquipmentLocationResponse(
            message="Equipment location and status updated successfully",
            equipment_id=equipment_id,
            checkout_id=checkout_id
        )
