import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is provided by the environment/runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

class Amenity(BaseModel):
    id: int
    name: str

class UnitResponse(BaseModel):
    id: int
    property_id: int
    unit_number: str
    monthly_rent: float
    square_footage: int
    availability_status: str
    amenities: List[Amenity]

class UpdateUnitRequest(BaseModel):
    monthly_rent: Optional[float] = None
    square_footage: Optional[int] = None
    availability_status: Optional[str] = None
    amenity_ids: Optional[List[int]] = None


def get_unit_with_amenities(db, property_id: int, unit_number: str) -> dict:
    # Fetch unit details
    unit_query = text("""
        SELECT id, property_id, unit_number, monthly_rent, square_footage, availability_status
        FROM units
        WHERE property_id = :property_id AND unit_number = :unit_number
    """)
    unit_row = db.execute(unit_query, {"property_id": property_id, "unit_number": unit_number}).fetchone()
    
    if not unit_row:
        raise HTTPException(status_code=404, detail="Unit not found")
    
    unit_id = unit_row[0]
    
    # Fetch amenities for this unit
    amenities_query = text("""
        SELECT a.id, a.name
        FROM amenities a
        JOIN unit_amenities ua ON a.id = ua.amenity_id
        WHERE ua.unit_id = :unit_id
    """)
    amenity_rows = db.execute(amenities_query, {"unit_id": unit_id}).fetchall()
    
    amenities = [{"id": row[0], "name": row[1]} for row in amenity_rows]
    
    return {
        "id": unit_row[0],
        "property_id": unit_row[1],
        "unit_number": unit_row[2],
        "monthly_rent": unit_row[3],
        "square_footage": unit_row[4],
        "availability_status": unit_row[5],
        "amenities": amenities
    }


@app.get("/properties/{property_id}/units/{unit_number}", response_model=UnitResponse, operation_id="get_unit_details")
def get_unit_details(property_id: int = Path(...), unit_number: str = Path(...)):
    db = SessionLocal()
    try:
        result = get_unit_with_amenities(db, property_id, unit_number)
        return result
    finally:
        db.close()


@app.put("/properties/{property_id}/units/{unit_number}", response_model=UnitResponse, operation_id="update_unit")
def update_unit(
    property_id: int = Path(...),
    unit_number: str = Path(...),
    body: UpdateUnitRequest = ...
):
    db = SessionLocal()
    try:
        # First verify the unit exists and get its ID
        unit_query = text("""
            SELECT id FROM units
            WHERE property_id = :property_id AND unit_number = :unit_number
        """)
        unit_row = db.execute(unit_query, {"property_id": property_id, "unit_number": unit_number}).fetchone()
        
        if not unit_row:
            raise HTTPException(status_code=404, detail="Unit not found")
        
        unit_id = unit_row[0]
        
        # Build dynamic UPDATE statement for unit fields
        updates = []
        params = {}
        
        if body.monthly_rent is not None:
            updates.append("monthly_rent = :monthly_rent")
            params["monthly_rent"] = body.monthly_rent
        
        if body.square_footage is not None:
            updates.append("square_footage = :square_footage")
            params["square_footage"] = body.square_footage
        
        if body.availability_status is not None:
            # Validate against CHECK constraint values
            valid_statuses = ["Available", "Occupied", "Under Maintenance", "Notice"]
            if body.availability_status not in valid_statuses:
                raise HTTPException(status_code=400, detail=f"Invalid availability_status. Must be one of: {valid_statuses}")
            updates.append("availability_status = :availability_status")
            params["availability_status"] = body.availability_status
        
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            sql = f"UPDATE units SET {', '.join(updates)} WHERE id = :unit_id"
            params["unit_id"] = unit_id
            db.execute(text(sql), params)
        
        # Handle amenity updates if provided
        if body.amenity_ids is not None:
            # Delete existing amenities for this unit
            delete_query = text("DELETE FROM unit_amenities WHERE unit_id = :unit_id")
            db.execute(delete_query, {"unit_id": unit_id})
            
            # Insert new amenities
            if body.amenity_ids:
                # Verify all amenity IDs exist
                placeholders = ", ".join([f":aid_{i}" for i in range(len(body.amenity_ids))])
                check_query = text(f"SELECT id FROM amenities WHERE id IN ({placeholders})")
                check_params = {f"aid_{i}": aid for i, aid in enumerate(body.amenity_ids)}
                existing_amenities = db.execute(check_query, check_params).fetchall()
                
                if len(existing_amenities) != len(body.amenity_ids):
                    raise HTTPException(status_code=400, detail="One or more amenity IDs do not exist")
                
                insert_query = text("INSERT INTO unit_amenities (unit_id, amenity_id) VALUES (:unit_id, :amenity_id)")
                for amenity_id in body.amenity_ids:
                    db.execute(insert_query, {"unit_id": unit_id, "amenity_id": amenity_id})
        
        db.commit()
        
        # Return updated unit with amenities
        result = get_unit_with_amenities(db, property_id, unit_number)
        return result
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/amenities", response_model=List[Amenity], operation_id="list_amenities")
def list_amenities():
    db = SessionLocal()
    try:
        query = text("SELECT id, name FROM amenities ORDER BY id")
        rows = db.execute(query).fetchall()
        return [{"id": row[0], "name": row[1]} for row in rows]
    finally:
        db.close()
