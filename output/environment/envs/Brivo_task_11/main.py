import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from typing import Optional, List

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ScheduledVisit(BaseModel):
    id: int
    visitor_name: str
    host_name: str
    scheduled_start: str
    status: str


class VisitDetails(BaseModel):
    id: int
    visitor_id: int
    visitor_name: str
    host_user_id: int
    host_name: str
    office_id: int
    office_name: str
    scheduled_start: str
    scheduled_end: Optional[str]
    actual_arrival: Optional[str]
    status: str


class CheckInRequest(BaseModel):
    actual_arrival: str


class CheckInResponse(BaseModel):
    id: int
    visitor_id: int
    host_user_id: int
    office_id: int
    scheduled_start: str
    actual_arrival: str
    status: str


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string supporting both T...Z and space-separated formats."""
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Try replacing space with T for space-separated format
        if ' ' in dt_str:
            dt_str_normalized = dt_str.replace(' ', 'T', 1)
            if dt_str_normalized.endswith('+00:00') or '+' in dt_str_normalized[10:] or '-' in dt_str_normalized[10:]:
                pass
            else:
                dt_str_normalized += '+00:00'
            return datetime.fromisoformat(dt_str_normalized)
        raise


@app.get("/visits/scheduled", response_model=List[ScheduledVisit], operation_id="find_scheduled_visits")
def find_scheduled_visits(
    office_name: str = Query(..., description="Name of the office"),
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format"),
):
    query = text("""
        SELECT 
            v.id,
            vis.full_name AS visitor_name,
            u.full_name AS host_name,
            v.scheduled_start,
            v.status
        FROM visits v
        JOIN visitors vis ON v.visitor_id = vis.id
        JOIN users u ON v.host_user_id = u.id
        JOIN offices o ON v.office_id = o.id
        WHERE o.name = :office_name
          AND DATE(v.scheduled_start) >= :start_date
          AND DATE(v.scheduled_start) <= :end_date
        ORDER BY v.scheduled_start ASC
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {
            "office_name": office_name,
            "start_date": start_date,
            "end_date": end_date,
        })
        rows = result.fetchall()
        
        visits = []
        for row in rows:
            visits.append(ScheduledVisit(
                id=row[0],
                visitor_name=row[1],
                host_name=row[2],
                scheduled_start=str(row[3]),
                status=row[4],
            ))
        
        return visits


@app.get("/visits/{visit_id}", response_model=VisitDetails, operation_id="get_visit_details")
def get_visit_details(visit_id: int):
    query = text("""
        SELECT 
            v.id,
            v.visitor_id,
            vis.full_name AS visitor_name,
            v.host_user_id,
            u.full_name AS host_name,
            v.office_id,
            o.name AS office_name,
            v.scheduled_start,
            v.scheduled_end,
            v.actual_arrival,
            v.status
        FROM visits v
        JOIN visitors vis ON v.visitor_id = vis.id
        JOIN users u ON v.host_user_id = u.id
        JOIN offices o ON v.office_id = o.id
        WHERE v.id = :visit_id
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"visit_id": visit_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Visit not found")
        
        return VisitDetails(
            id=row[0],
            visitor_id=row[1],
            visitor_name=row[2],
            host_user_id=row[3],
            host_name=row[4],
            office_id=row[5],
            office_name=row[6],
            scheduled_start=str(row[7]) if row[7] else None,
            scheduled_end=str(row[8]) if row[8] else None,
            actual_arrival=str(row[9]) if row[9] else None,
            status=row[10],
        )


@app.put("/visits/{visit_id}/checkin", response_model=CheckInResponse, operation_id="check_in_visitor")
def check_in_visitor(visit_id: int, body: CheckInRequest):
    # Parse the actual arrival datetime
    try:
        parsed_arrival = parse_datetime(body.actual_arrival)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {str(e)}")
    
    # Format back to standard ISO-like string for storage (SQLite DATETIME typically stores as 'YYYY-MM-DD HH:MM:SS')
    arrival_str = parsed_arrival.strftime("%Y-%m-%d %H:%M:%S")
    
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="No authenticated user context available")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID in context")
    
    with engine.begin() as conn:
        # First, verify the visit exists and is in 'scheduled' status
        select_query = text("""
            SELECT id, status, visitor_id, host_user_id, office_id, scheduled_start
            FROM visits
            WHERE id = :visit_id
        """)
        result = conn.execute(select_query, {"visit_id": visit_id})
        visit_row = result.fetchone()
        
        if not visit_row:
            raise HTTPException(status_code=404, detail="Visit not found")
        
        if visit_row[1] != 'scheduled':
            raise HTTPException(status_code=400, detail=f"Cannot check in visit with status '{visit_row[1]}'. Only 'scheduled' visits can be checked in.")
        
        # Update the visit record
        update_query = text("""
            UPDATE visits
            SET actual_arrival = :actual_arrival,
                status = 'checked_in',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :visit_id
        """)
        conn.execute(update_query, {
            "actual_arrival": arrival_str,
            "visit_id": visit_id,
        })
        
        # Insert into visit_status_history
        history_insert = text("""
            INSERT INTO visit_status_history (visit_id, status, changed_by_user_id, notes)
            VALUES (:visit_id, 'checked_in', :changed_by_user_id, :notes)
        """)
        conn.execute(history_insert, {
            "visit_id": visit_id,
            "changed_by_user_id": current_user_id,
            "notes": f"Visitor checked in at {arrival_str}.",
        })
        
        # Fetch the updated visit data for response
        fetch_query = text("""
            SELECT id, visitor_id, host_user_id, office_id, scheduled_start, actual_arrival, status
            FROM visits
            WHERE id = :visit_id
        """)
        final_result = conn.execute(fetch_query, {"visit_id": visit_id})
        final_row = final_result.fetchone()
        
        return CheckInResponse(
            id=final_row[0],
            visitor_id=final_row[1],
            host_user_id=final_row[2],
            office_id=final_row[3],
            scheduled_start=str(final_row[4]),
            actual_arrival=str(final_row[5]),
            status=final_row[6],
        )
