import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CommissaryHub API")


class ReservationCreate(BaseModel):
    resource_id: int
    start_time: str
    end_time: str
    notes: Optional[str] = None


class ResourceOut(BaseModel):
    id: int
    name: str
    resource_type: str
    description: str
    is_active: bool


class ReservationOut(BaseModel):
    id: int
    user_id: int
    resource_id: int
    start_time: str
    end_time: str
    status: str
    notes: Optional[str]
    created_at: str
    updated_at: str


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime strings, handling both 'T' and space separators."""
    # Replace 'Z' with '+00:00' if present for fromisoformat compatibility in older Python versions, 
    # though modern Python handles Z. We'll try standard parsing first.
    dt_str_clean = dt_str.replace('Z', '+00:00')
    
    # Handle space separator instead of T
    if ' ' in dt_str_clean and 'T' not in dt_str_clean:
        parts = dt_str_clean.split(' ')
        if len(parts) == 2:
            dt_str_clean = f"{parts[0]}T{parts[1]}"
            
    return datetime.fromisoformat(dt_str_clean)


@app.post("/reservations", response_model=ReservationOut, operation_id="create_reservation")
def create_reservation(reservation_in: ReservationCreate):
    current_user_id_env = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_env:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(current_user_id_env)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    try:
        start_dt = parse_datetime(reservation_in.start_time)
        end_dt = parse_datetime(reservation_in.end_time)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {str(e)}")

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    db = SessionLocal()
    try:
        # Check if resource exists and is active
        res_query = text("""
            SELECT id, name, resource_type, is_active 
            FROM resources 
            WHERE id = :resource_id
        """)
        res_row = db.execute(res_query, {"resource_id": reservation_in.resource_id}).fetchone()
        
        if not res_row:
            raise HTTPException(status_code=404, detail="Resource not found")
        
        if not res_row.is_active:
            raise HTTPException(status_code=400, detail="Resource is not active")

        # Check for overlapping reservations
        # Overlap condition: existing.start < new.end AND existing.end > new.start
        overlap_query = text("""
            SELECT COUNT(*) as cnt
            FROM reservations
            WHERE resource_id = :resource_id
              AND status != 'cancelled'
              AND start_time < :new_end
              AND end_time > :new_start
        """)
        
        overlap_result = db.execute(overlap_query, {
            "resource_id": reservation_in.resource_id,
            "new_start": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "new_end": end_dt.strftime("%Y-%m-%d %H:%M:%S")
        }).fetchone()
        
        if overlap_result.cnt > 0:
            raise HTTPException(status_code=409, detail="Resource is already booked for this time slot")

        # Insert new reservation
        insert_query = text("""
            INSERT INTO reservations (user_id, resource_id, start_time, end_time, status, notes, created_at, updated_at)
            VALUES (:user_id, :resource_id, :start_time, :end_time, 'confirmed', :notes, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        
        result = db.execute(insert_query, {
            "user_id": user_id,
            "resource_id": reservation_in.resource_id,
            "start_time": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "notes": reservation_in.notes
        })
        
        db.commit()
        
        new_id = result.lastrowid
        
        # Fetch the created record to return full details including timestamps
        fetch_query = text("""
            SELECT id, user_id, resource_id, start_time, end_time, status, notes, created_at, updated_at
            FROM reservations
            WHERE id = :id
        """)
        
        row = db.execute(fetch_query, {"id": new_id}).fetchone()
        
        return ReservationOut(
            id=row.id,
            user_id=row.user_id,
            resource_id=row.resource_id,
            start_time=str(row.start_time),
            end_time=str(row.end_time),
            status=row.status,
            notes=row.notes,
            created_at=str(row.created_at),
            updated_at=str(row.updated_at)
        )
        
    finally:
        db.close()


@app.get("/resources", response_model=list[ResourceOut], operation_id="find_resources")
def find_resources(resource_type: str = Query(..., description="Type of resource to filter by ('station' or 'equipment')")):
    if resource_type not in ("station", "equipment"):
        raise HTTPException(status_code=400, detail="Invalid resource type. Must be 'station' or 'equipment'.")

    db = SessionLocal()
    try:
        query = text("""
            SELECT id, name, resource_type, description, is_active
            FROM resources
            WHERE resource_type = :rtype AND is_active = 1
        """)
        
        rows = db.execute(query, {"rtype": resource_type}).fetchall()
        
        return [
            ResourceOut(
                id=r.id,
                name=r.name,
                resource_type=r.resource_type,
                description=r.description,
                is_active=bool(r.is_active)
            )
            for r in rows
        ]
    finally:
        db.close()
