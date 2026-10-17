import os
from typing import Optional, List

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from contextlib import asynccontextmanager

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

class CampsiteResponse(BaseModel):
    id: int
    name: str
    site_type: str
    base_price_per_night: float
    is_active: int

class SurchargeRequest(BaseModel):
    surcharge_amount: float = Field(..., description="The surcharge amount to apply per night")
    start_date: str = Field(..., description="Start date for the surcharge period (YYYY-MM-DD)")
    end_date: str = Field(..., description="End date for the surcharge period (YYYY-MM-DD)")
    description: Optional[str] = Field(None, description="Optional description for the surcharge")

class SurchargeResponse(BaseModel):
    id: int
    campsite_id: int
    surcharge_amount: float
    start_date: str
    end_date: str
    description: Optional[str]
    created_by: int

app = FastAPI()

@app.get("/campsites", response_model=List[CampsiteResponse], operation_id="find_campsites")
def find_campsites(
    site_type: Optional[str] = Query(None, description="Type of campsite (tent, rv, cabin, group)"),
    is_active: Optional[int] = Query(None, description="Whether the campsite is active (1 for active, 0 for inactive)")
):
    query = """
        SELECT id, name, site_type, base_price_per_night, is_active
        FROM campsites
        WHERE 1=1
    """
    params = {}
    
    if site_type is not None:
        query += " AND site_type = :site_type"
        params["site_type"] = site_type
        
    if is_active is not None:
        query += " AND is_active = :is_active"
        params["is_active"] = is_active

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        rows = result.fetchall()
        
        return [
            CampsiteResponse(
                id=row.id,
                name=row.name,
                site_type=row.site_type,
                base_price_per_night=row.base_price_per_night,
                is_active=row.is_active
            )
            for row in rows
        ]

@app.post("/seasonal-surcharges", response_model=List[SurchargeResponse], operation_id="add_july_tent_surcharge")
def add_july_tent_surcharge(request: SurchargeRequest):
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user ID format")

    # Find all active tent sites
    select_query = """
        SELECT id 
        FROM campsites 
        WHERE site_type = 'tent' AND is_active = 1
    """
    
    inserted_records = []
    
    with engine.begin() as conn:
        # Verify user exists
        user_check = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": current_user_id}).fetchone()
        if not user_check:
            raise HTTPException(status_code=404, detail="Current user does not exist")

        tent_sites_result = conn.execute(text(select_query)).fetchall()
        tent_site_ids = [row.id for row in tent_sites_result]

        if not tent_site_ids:
            return []

        insert_query = """
            INSERT INTO seasonal_surcharges (campsite_id, surcharge_amount, start_date, end_date, description, created_by)
            VALUES (:campsite_id, :surcharge_amount, :start_date, :end_date, :description, :created_by)
            RETURNING id, campsite_id, surcharge_amount, start_date, end_date, description, created_by
        """

        for site_id in tent_site_ids:
            params = {
                "campsite_id": site_id,
                "surcharge_amount": request.surcharge_amount,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "description": request.description,
                "created_by": current_user_id
            }
            
            result = conn.execute(text(insert_query), params)
            row = result.fetchone()
            
            if row:
                inserted_records.append(SurchargeResponse(
                    id=row.id,
                    campsite_id=row.campsite_id,
                    surcharge_amount=row.surcharge_amount,
                    start_date=row.start_date,
                    end_date=row.end_date,
                    description=row.description,
                    created_by=row.created_by
                ))

    return inserted_records
