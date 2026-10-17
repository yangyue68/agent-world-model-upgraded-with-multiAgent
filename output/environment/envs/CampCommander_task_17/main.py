import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class RatePlanCreate(BaseModel):
    name: str = Field(..., description="Name of the rate plan")
    description: Optional[str] = Field(None, description="Description of the rate plan")
    discount_percent: float = Field(..., ge=0, le=100, description="Discount percentage offered by the rate plan")
    min_nights: int = Field(..., gt=0, description="Minimum number of nights required to qualify for the rate plan")
    valid_from: str = Field(..., description="Start date when the rate plan becomes valid (YYYY-MM-DD)")
    valid_to: str = Field(..., description="End date when the rate plan expires (YYYY-MM-DD)")


class RatePlanResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    discount_percent: float
    min_nights: int
    valid_from: str
    valid_to: str
    applies_to_all_site_types: bool


def parse_date(date_str: str) -> date:
    """Parse ISO-8601 date string supporting YYYY-MM-DD and other common formats."""
    try:
        # Try standard YYYY-MM-DD first
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        pass
    
    # Try parsing as full ISO timestamp with T separator
    if "T" in date_str:
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.date()
        except ValueError:
            pass
    
    # Try space-separated format
    if " " in date_str:
        try:
            dt = datetime.strptime(date_str.split()[0], "%Y-%m-%d")
            return dt.date()
        except ValueError:
            pass
            
    raise ValueError(f"Unable to parse date: {date_str}")


@app.post("/rate_plans", response_model=RatePlanResponse, operation_id="create_rate_plan")
def create_rate_plan(payload: RatePlanCreate):
    """
    Creates a new rate plan with the specified parameters. 
    The rate plan will apply to all site types as requested.
    """
    # Parse dates
    try:
        v_from = parse_date(payload.valid_from)
        v_to = parse_date(payload.valid_to)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Validate date range
    if v_from > v_to:
        raise HTTPException(status_code=422, detail="valid_from must be less than or equal to valid_to")

    db = SessionLocal()
    try:
        # Insert into rate_plans table
        # applies_to_all_site_types defaults to 1 in schema, which matches requirement "applicable to all site types"
        stmt = text("""
            INSERT INTO rate_plans (name, description, discount_percent, min_nights, valid_from, valid_to, applies_to_all_site_types)
            VALUES (:name, :description, :discount_percent, :min_nights, :valid_from, :valid_to, 1)
        """)
        
        result = db.execute(stmt, {
            "name": payload.name,
            "description": payload.description,
            "discount_percent": payload.discount_percent,
            "min_nights": payload.min_nights,
            "valid_from": v_from.isoformat(),
            "valid_to": v_to.isoformat()
        })
        
        db.commit()
        
        # Get the inserted ID
        last_id = result.lastrowid
        
        # Fetch the created record to ensure consistency and get default values like created_at/updated_at if needed, 
        # though we can construct the response from input + known defaults.
        # Let's fetch it back to be safe and accurate regarding any DB-side transformations.
        select_stmt = text("""
            SELECT id, name, description, discount_percent, min_nights, valid_from, valid_to, applies_to_all_site_types
            FROM rate_plans
            WHERE id = :id
        """)
        
        row = db.execute(select_stmt, {"id": last_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created rate plan")
            
        return RatePlanResponse(
            id=row.id,
            name=row.name,
            description=row.description,
            discount_percent=float(row.discount_percent),
            min_nights=int(row.min_nights),
            valid_from=str(row.valid_from),
            valid_to=str(row.valid_to),
            applies_to_all_site_types=bool(row.applies_to_all_site_types)
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        db.close()
