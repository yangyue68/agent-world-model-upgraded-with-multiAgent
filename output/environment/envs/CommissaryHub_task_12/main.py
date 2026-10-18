import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class HealthInspectionCreate(BaseModel):
    kitchen_area_name: str = Field(..., description="Name of the kitchen area being inspected")
    score: int = Field(..., ge=0, le=100, description="Health inspection score (0-100)")
    inspection_date: date = Field(..., description="Date of the inspection in YYYY-MM-DD format")
    notes: Optional[str] = Field(None, description="Additional notes about the inspection")


class HealthInspectionResponse(BaseModel):
    id: int
    kitchen_area_id: int
    score: int
    inspection_date: date
    notes: Optional[str]
    created_by: int
    created_at: datetime
    updated_at: datetime


@app.post("/health_inspections", response_model=HealthInspectionResponse, operation_id="record_health_inspection")
def record_health_inspection(payload: HealthInspectionCreate):
    """
    Records a new health inspection score for a specified kitchen area.
    The current user (from AWM_CURRENT_USER_ID env var) is recorded as the inspector.
    """
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Current user not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")

    db = SessionLocal()
    try:
        # Find kitchen_area_id by name
        result = db.execute(
            text("SELECT id FROM kitchen_areas WHERE name = :name"),
            {"name": payload.kitchen_area_name}
        )
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Kitchen area '{payload.kitchen_area_name}' not found")
        
        kitchen_area_id = row[0]

        # Insert new health inspection
        now = datetime.utcnow()
        insert_result = db.execute(
            text("""
                INSERT INTO health_inspections 
                (kitchen_area_id, score, inspection_date, notes, created_by, created_at, updated_at)
                VALUES (:kitchen_area_id, :score, :inspection_date, :notes, :created_by, :created_at, :updated_at)
            """),
            {
                "kitchen_area_id": kitchen_area_id,
                "score": payload.score,
                "inspection_date": payload.inspection_date.isoformat(),
                "notes": payload.notes,
                "created_by": current_user_id,
                "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        db.commit()
        
        # Fetch the inserted record to return full details including generated IDs and timestamps
        last_inserted_id = insert_result.lastrowid
        
        fetch_result = db.execute(
            text("""
                SELECT id, kitchen_area_id, score, inspection_date, notes, created_by, created_at, updated_at
                FROM health_inspections
                WHERE id = :id
            """),
            {"id": last_inserted_id}
        )
        fetched_row = fetch_result.fetchone()
        
        if not fetched_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve inserted inspection record")

        # Parse dates back to proper types for Pydantic model
        insp_date = date.fromisoformat(fetched_row[3])
        created_dt = datetime.strptime(fetched_row[6], "%Y-%m-%d %H:%M:%S")
        updated_dt = datetime.strptime(fetched_row[7], "%Y-%m-%d %H:%M:%S")

        return HealthInspectionResponse(
            id=fetched_row[0],
            kitchen_area_id=fetched_row[1],
            score=fetched_row[2],
            inspection_date=insp_date,
            notes=fetched_row[4],
            created_by=fetched_row[5],
            created_at=created_dt,
            updated_at=updated_dt
        )

    finally:
        db.close()
