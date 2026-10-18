import os
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class ReservationCreateRequest(BaseModel):
    equipment_id: int
    team_id: int
    user_id: int
    pickup_location_id: int
    start_datetime: str
    end_datetime: str
    recurrence_rule: str
    recurrence_end_date: str


class ReservationResponse(BaseModel):
    id: int
    equipment_id: int
    team_id: Optional[int]
    user_id: int
    pickup_location_id: Optional[int]
    start_datetime: str
    end_datetime: str
    recurrence_rule: Optional[str]
    recurrence_end_date: Optional[str]
    status: str


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both T...Z and space-separated forms."""
    dt_str = dt_str.strip()
    # Handle Z suffix
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    # Try parsing with various formats
    for fmt in [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
    ]:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    # Fallback to fromisoformat
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except Exception:
        raise ValueError(f"Cannot parse datetime: {dt_str}")


def parse_date(d_str: str) -> date:
    """Parse ISO-8601 date string."""
    d_str = d_str.strip()
    for fmt in ["%Y-%m-%d"]:
        try:
            return datetime.strptime(d_str, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(d_str)
    except Exception:
        raise ValueError(f"Cannot parse date: {d_str}")


@app.post("/reservations", response_model=ReservationResponse, operation_id="create_recurring_reservation")
def create_recurring_reservation(req: ReservationCreateRequest):
    # Parse datetimes
    try:
        start_dt = parse_datetime(req.start_datetime)
        end_dt = parse_datetime(req.end_datetime)
        rec_end_date = parse_date(req.recurrence_end_date)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Validate that the user exists
    with SessionLocal() as session:
        user_result = session.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": req.user_id})
        if user_result.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"User with ID {req.user_id} not found")

        # Validate that the equipment exists
        equip_result = session.execute(text("SELECT id FROM equipment WHERE id = :eid"), {"eid": req.equipment_id})
        if equip_result.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"Equipment with ID {req.equipment_id} not found")

        # Validate that the team exists (team_id can be null in schema but request requires it)
        team_result = session.execute(text("SELECT id FROM teams WHERE id = :tid"), {"tid": req.team_id})
        if team_result.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"Team with ID {req.team_id} not found")

        # Validate that the pickup location exists
        loc_result = session.execute(text("SELECT id FROM locations WHERE id = :lid"), {"lid": req.pickup_location_id})
        if loc_result.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"Location with ID {req.pickup_location_id} not found")

        # Determine created_by / actor from environment
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        # The reservation's user_id is explicitly provided in the request body per spec.
        # We use the provided user_id for the reservation record.

        # Insert the reservation
        insert_stmt = text("""
            INSERT INTO reservations (
                equipment_id, team_id, user_id, pickup_location_id,
                start_datetime, end_datetime, recurrence_rule, recurrence_end_date,
                status, notes, created_at, updated_at
            ) VALUES (
                :equipment_id, :team_id, :user_id, :pickup_location_id,
                :start_datetime, :end_datetime, :recurrence_rule, :recurrence_end_date,
                'active', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """)

        result = session.execute(insert_stmt, {
            "equipment_id": req.equipment_id,
            "team_id": req.team_id,
            "user_id": req.user_id,
            "pickup_location_id": req.pickup_location_id,
            "start_datetime": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "end_datetime": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "recurrence_rule": req.recurrence_rule,
            "recurrence_end_date": rec_end_date.strftime("%Y-%m-%d"),
        })
        session.commit()

        new_id = result.lastrowid

        # Fetch the inserted row to return
        fetch_stmt = text("""
            SELECT id, equipment_id, team_id, user_id, pickup_location_id,
                   start_datetime, end_datetime, recurrence_rule, recurrence_end_date, status
            FROM reservations WHERE id = :rid
        """)
        row = session.execute(fetch_stmt, {"rid": new_id}).fetchone()

        if row is None:
            raise HTTPException(status_code=500, detail="Failed to retrieve created reservation")

        resp = ReservationResponse(
            id=row[0],
            equipment_id=row[1],
            team_id=row[2],
            user_id=row[3],
            pickup_location_id=row[4],
            start_datetime=row[5],
            end_datetime=row[6],
            recurrence_rule=row[7],
            recurrence_end_date=row[8],
            status=row[9],
        )
        return resp
