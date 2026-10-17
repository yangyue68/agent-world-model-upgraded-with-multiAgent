import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from contextlib import asynccontextmanager

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)


class VisitResponse(BaseModel):
    id: int
    visitor_id: int
    location_id: int
    host_id: int
    scheduled_date: str
    status: str


class UpdateVisitRequest(BaseModel):
    check_out_time: str = Field(..., description="Checkout datetime in ISO format (e.g., 2025-04-15T18:45:00Z)")


class UpdatedVisitResponse(BaseModel):
    id: int
    visitor_id: int
    location_id: int
    host_id: int
    scheduled_date: str
    check_in_time: Optional[str]
    check_out_time: Optional[str]
    status: str


app = FastAPI()


@app.get("/visits", response_model=List[VisitResponse], operation_id="find_visit_by_visitor_location_and_date")
def find_visit(
    visitor_name: str = Query(..., description="Full name of the visitor"),
    location_name: str = Query(..., description="Name of the location"),
    scheduled_date: str = Query(..., description="Scheduled date of the visit in YYYY-MM-DD format"),
):
    """
    Locate a visit record for a specific visitor at a specific location on a given date.
    Returns the visit ID, visitor ID, location ID, host ID, and status.
    """
    query = text("""
        SELECT 
            v.id,
            v.visitor_id,
            v.location_id,
            v.host_id,
            v.scheduled_date,
            v.status
        FROM visits v
        JOIN visitors vis ON v.visitor_id = vis.id
        JOIN locations loc ON v.location_id = loc.id
        WHERE vis.full_name = :visitor_name
          AND loc.name = :location_name
          AND v.scheduled_date = :scheduled_date
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {
            "visitor_name": visitor_name,
            "location_name": location_name,
            "scheduled_date": scheduled_date
        })
        rows = result.fetchall()

    if not rows:
        return []

    # The spec says it returns an array of objects matching the properties
    output = []
    for row in rows:
        output.append({
            "id": row[0],
            "visitor_id": row[1],
            "location_id": row[2],
            "host_id": row[3],
            "scheduled_date": str(row[4]),
            "status": row[5]
        })
    
    return output


@app.put("/visits/{visit_id}", response_model=UpdatedVisitResponse, operation_id="update_visit_checkout")
def update_visit_checkout(
    visit_id: int = Path(..., description="ID of the visit to update"),
    body: UpdateVisitRequest = None
):
    """
    Record the departure time for a visit and update its status to checked out.
    Requires the visit ID, checkout time, and ensures the visit exists and is not already checked out.
    """
    if body is None:
        raise HTTPException(status_code=422, detail="Body required")

    # First, verify the visit exists and get current state
    select_query = text("""
        SELECT id, visitor_id, location_id, host_id, scheduled_date, check_in_time, check_out_time, status
        FROM visits
        WHERE id = :visit_id
    """)

    with engine.begin() as conn:
        res = conn.execute(select_query, {"visit_id": visit_id})
        row = res.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Visit not found")

        current_status = row[7]
        
        # Business logic: Ensure the visit is not already checked out
        if current_status == 'checked_out':
            raise HTTPException(status_code=400, detail="Visit is already checked out")

        # Prepare the update
        # We need to normalize the input timestamp if necessary, but SQLite stores what we give it.
        # The prompt asks to record departure at 6:45 PM for 2025-04-15.
        # Input example: 2025-04-15T18:45:00Z
        
        update_query = text("""
            UPDATE visits
            SET check_out_time = :check_out_time,
                status = 'checked_out',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :visit_id
        """)

        conn.execute(update_query, {
            "visit_id": visit_id,
            "check_out_time": body.check_out_time
        })

        # Fetch the updated record to return
        final_res = conn.execute(select_query, {"visit_id": visit_id})
        final_row = final_res.fetchone()

    if not final_row:
         raise HTTPException(status_code=500, detail="Failed to retrieve updated visit")

    return {
        "id": final_row[0],
        "visitor_id": final_row[1],
        "location_id": final_row[2],
        "host_id": final_row[3],
        "scheduled_date": str(final_row[4]),
        "check_in_time": str(final_row[5]) if final_row[5] else None,
        "check_out_time": str(final_row[6]) if final_row[6] else None,
        "status": final_row[7]
    }
