import os
from datetime import date, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class BulkUpdateMinStayRequest(BaseModel):
    campsite_ids: list[int] = Field(..., description="List of RV site IDs to update")
    start_date: str = Field(..., format="date", description="Start date of the period to update (inclusive)")
    end_date: str = Field(..., format="date", description="End date of the period to update (inclusive)")
    min_nights: int = Field(..., description="New minimum stay requirement in nights")


class BulkUpdateMinStayResponse(BaseModel):
    message: str
    updated_count: int


@app.get("/campsites/rv", operation_id="find_rv_sites")
def find_rv_sites():
    """Retrieve all active campsites that are of type 'rv'."""
    query = text("""
        SELECT id, name
        FROM campsites
        WHERE site_type = :site_type AND is_active = 1
        ORDER BY id
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"site_type": "rv"})
        rows = result.fetchall()
    
    return [{"id": row.id, "name": row.name} for row in rows]


@app.post("/minimum_stays/bulk_update", operation_id="bulk_update_min_stay")
def bulk_update_min_stay(request: BulkUpdateMinStayRequest):
    """Updates the minimum stay requirement for specified RV sites during a given date range."""
    # Validate dates
    try:
        start_dt = date.fromisoformat(request.start_date)
        end_dt = date.fromisoformat(request.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    
    if start_dt > end_dt:
        raise HTTPException(status_code=400, detail="start_date cannot be after end_date.")
    
    if request.min_nights <= 0:
        raise HTTPException(status_code=400, detail="min_nights must be greater than 0.")
    
    if not request.campsite_ids:
        raise HTTPException(status_code=400, detail="campsite_ids cannot be empty.")
    
    # Verify all provided campsite IDs are valid active RV sites
    placeholders = ", ".join([f":cid_{i}" for i in range(len(request.campsite_ids))])
    params = {f"cid_{i}": cid for i, cid in enumerate(request.campsite_ids)}
    
    verify_query = text(f"""
        SELECT id
        FROM campsites
        WHERE id IN ({placeholders}) AND site_type = 'rv' AND is_active = 1
    """)
    
    with engine.connect() as conn:
        result = conn.execute(verify_query, params)
        valid_ids = {row.id for row in result.fetchall()}
        
        invalid_ids = [cid for cid in request.campsite_ids if cid not in valid_ids]
        if invalid_ids:
            raise HTTPException(
                status_code=400, 
                detail=f"The following campsite IDs are not valid active RV sites: {invalid_ids}"
            )
        
        # Get current user ID from environment for audit purposes if needed, 
        # though the schema doesn't have an actor column on minimum_stays.
        # The task says "read that environment variable at request time" when writing actor columns.
        # Since minimum_stays has no created_by/author_user_id column, we don't need it for INSERT/UPDATE logic here.
        
        updated_count = 0
        
        for campsite_id in request.campsite_ids:
            # Check if a record already exists for this campsite and overlapping/exact date range?
            # The unique constraint is (campsite_id, start_date, end_date).
            # We want to update the minimum stay for the specific Memorial Day weekend dates.
            # However, the existing data might have broader ranges like May 1 - May 23.
            # The API spec says "Updates the minimum stay requirement ... during a given date range".
            # It implies creating or updating records specifically for that range.
            
            # Let's look at the existing data structure. 
            # Existing: campsite 1, 2024-05-01 to 2024-05-23, min_nights 2.
            # Task: Update min stay for RV sites to 3 nights for Memorial Day weekend (Fri-Mon).
            # Memorial Day 2024 was May 27th. So Fri May 24, Sat May 25, Sun May 26, Mon May 27.
            # Wait, looking at initial snapshot, there is a reservation for campsite 3 starting 2024-05-24.
            # And minimum stays exist for May 1-23. There is NO entry for May 24-27 yet.
            
            # The endpoint takes start_date and end_date. If I pass 2024-05-24 and 2024-05-27,
            # I should insert a new record into minimum_stays because one doesn't exist for those exact bounds.
            # Or does it mean modifying existing overlapping ones? 
            # Given the UNIQUE constraint on (campsite_id, start_date, end_date), 
            # inserting a new distinct range is safe. Updating requires matching the key.
            
            # Strategy: Try to UPDATE first. If no rows affected, INSERT.
            # But wait, if there's an existing broad rule (May 1-23), and we add a narrow rule (May 24-27),
            # they don't conflict due to different keys.
            
            # Let's assume the caller provides the exact start/end dates for the holiday weekend.
            # For Memorial Day 2024: Start 2024-05-24, End 2024-05-27.
            
            # Attempt Update
            update_stmt = text("""
                UPDATE minimum_stays
                SET min_nights = :min_nights, updated_at = CURRENT_TIMESTAMP
                WHERE campsite_id = :campsite_id AND start_date = :start_date AND end_date = :end_date
            """)
            res = conn.execute(update_stmt, {
                "min_nights": request.min_nights,
                "campsite_id": campsite_id,
                "start_date": request.start_date,
                "end_date": request.end_date
            })
            
            if res.rowcount == 0:
                # Insert new record
                insert_stmt = text("""
                    INSERT INTO minimum_stays (campsite_id, start_date, end_date, min_nights, created_at, updated_at)
                    VALUES (:campsite_id, :start_date, :end_date, :min_nights, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """)
                conn.execute(insert_stmt, {
                    "campsite_id": campsite_id,
                    "start_date": request.start_date,
                    "end_date": request.end_date,
                    "min_nights": request.min_nights
                })
            
            updated_count += 1
            
        conn.commit()
        
    return BulkUpdateMinStayResponse(
        message=f"Successfully updated minimum stay requirements for {updated_count} campsites.",
        updated_count=updated_count
    )
