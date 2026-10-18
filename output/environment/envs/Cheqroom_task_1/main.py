import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine: Engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ReservationRequest(BaseModel):
    team_name: str = Field(..., description="Name of the team requesting the reservation")
    equipment_name: str = Field(..., description="Name of the equipment being reserved")
    location_name: str = Field(..., description="Name of the location where the equipment will be used")
    assigned_user_username: str = Field(..., description="Username of the user to whom the reservation is assigned")
    project_reference: Optional[str] = Field(None, description="Reference code of the project associated with this reservation")
    start_time: str = Field(..., description="Start datetime of the reservation in ISO format (YYYY-MM-DDTHH:MM:SS)")
    end_time: str = Field(..., description="End datetime of the reservation in ISO format (YYYY-MM-DDTHH:MM:SS)")


class ReservationResponse(BaseModel):
    id: int
    team_id: int
    equipment_id: int
    location_id: int
    assigned_user_id: int
    project_id: Optional[int]
    start_time: str
    end_time: str
    status: str
    created_by: int
    created_at: str
    updated_at: str


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both T...Z and space-separated forms."""
    # Replace 'T' with space if present for uniform parsing, remove trailing Z
    cleaned = dt_str.replace("T", " ").replace("Z", "")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid datetime format: {dt_str}")


@app.post("/reservations", response_model=ReservationResponse, operation_id="create_reservation")
def create_reservation(req: ReservationRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=500, detail="Current user ID not set in environment")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID in environment")

    # Parse datetimes
    start_dt = parse_datetime(req.start_time)
    end_dt = parse_datetime(req.end_time)

    if end_dt <= start_dt:
        raise HTTPException(status_code=422, detail="end_time must be after start_time")

    with engine.connect() as conn:
        # Look up team by name
        result = conn.execute(text("SELECT id FROM teams WHERE name = :name"), {"name": req.team_name})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Team '{req.team_name}' not found")
        team_id = row[0]

        # Look up equipment by name
        result = conn.execute(text("SELECT id FROM equipment WHERE name = :name"), {"name": req.equipment_name})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Equipment '{req.equipment_name}' not found")
        equipment_id = row[0]

        # Look up location by name
        result = conn.execute(text("SELECT id FROM locations WHERE name = :name"), {"name": req.location_name})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Location '{req.location_name}' not found")
        location_id = row[0]

        # Look up assigned user by username
        result = conn.execute(text("SELECT id FROM users WHERE username = :username"), {"username": req.assigned_user_username})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"User '{req.assigned_user_username}' not found")
        assigned_user_id = row[0]

        # Look up project by reference (optional)
        project_id = None
        if req.project_reference:
            result = conn.execute(text("SELECT id FROM projects WHERE reference = :ref"), {"ref": req.project_reference})
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Project '{req.project_reference}' not found")
            project_id = row[0]

        # Verify created_by user exists
        result = conn.execute(text("SELECT id FROM users WHERE id = :id"), {"id": current_user_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"Created-by user ID {current_user_id} not found")

        # Insert reservation
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

        insert_result = conn.execute(
            text("""
                INSERT INTO reservations (team_id, equipment_id, location_id, assigned_user_id, project_id, start_time, end_time, status, created_by, created_at, updated_at)
                VALUES (:team_id, :equipment_id, :location_id, :assigned_user_id, :project_id, :start_time, :end_time, :status, :created_by, :created_at, :updated_at)
            """),
            {
                "team_id": team_id,
                "equipment_id": equipment_id,
                "location_id": location_id,
                "assigned_user_id": assigned_user_id,
                "project_id": project_id,
                "start_time": start_str,
                "end_time": end_str,
                "status": "confirmed",
                "created_by": current_user_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        conn.commit()

        new_id = insert_result.lastrowid

        # Fetch the inserted row to get exact stored values
        result = conn.execute(
            text("""
                SELECT id, team_id, equipment_id, location_id, assigned_user_id, project_id, start_time, end_time, status, created_by, created_at, updated_at
                FROM reservations WHERE id = :id
            """),
            {"id": new_id},
        )
        row = result.fetchone()

        return ReservationResponse(
            id=row[0],
            team_id=row[1],
            equipment_id=row[2],
            location_id=row[3],
            assigned_user_id=row[4],
            project_id=row[5],
            start_time=str(row[6]),
            end_time=str(row[7]),
            status=row[8],
            created_by=row[9],
            created_at=str(row[10]),
            updated_at=str(row[11]),
        )
