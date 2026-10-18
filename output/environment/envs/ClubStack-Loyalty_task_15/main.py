import os
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PauseRequest(BaseModel):
    pause_start_date: str
    duration_days: Optional[int] = 30


class MembershipResponse(BaseModel):
    id: int
    user_id: int
    membership_type: str
    start_date: str
    end_date: Optional[str]
    status: str


class PauseResponse(BaseModel):
    id: int
    membership_id: int
    pause_start_date: str
    pause_end_date: str
    status: str


@app.get("/memberships", response_model=list[MembershipResponse], operation_id="find_active_memberships")
def find_active_memberships(
    user_id: int = Query(..., description="Internal ID of the user whose memberships are being retrieved"),
    status: str = Query(..., description="Status filter for the membership (e.g., 'active')"),
):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, user_id, membership_type, start_date, end_date, status
                FROM memberships
                WHERE user_id = :user_id AND status = :status
            """),
            {"user_id": user_id, "status": status},
        )
        rows = result.fetchall()
        return [
            MembershipResponse(
                id=row.id,
                user_id=row.user_id,
                membership_type=row.membership_type,
                start_date=str(row.start_date),
                end_date=str(row.end_date) if row.end_date else None,
                status=row.status,
            )
            for row in rows
        ]


@app.post("/memberships/{membership_id}/pause", response_model=PauseResponse, operation_id="pause_membership")
def pause_membership(membership_id: int, body: PauseRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Parse dates
    try:
        pause_start = date.fromisoformat(body.pause_start_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pause_start_date format. Use YYYY-MM-DD.")
    
    duration = body.duration_days if body.duration_days is not None else 30
    if duration <= 0:
        raise HTTPException(status_code=400, detail="Duration must be positive")
        
    pause_end = pause_start + timedelta(days=duration - 1)

    with engine.begin() as conn:
        # Verify membership exists, belongs to user, and is active
        mem_result = conn.execute(
            text("""
                SELECT id, user_id, status
                FROM memberships
                WHERE id = :membership_id
            """),
            {"membership_id": membership_id},
        )
        membership = mem_result.fetchone()
        
        if not membership:
            raise HTTPException(status_code=404, detail="Membership not found")
        
        if membership.user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Forbidden: You do not own this membership")
            
        if membership.status != "active":
            raise HTTPException(status_code=400, detail=f"Cannot pause membership with status '{membership.status}'. Must be 'active'.")

        # Check for overlapping pauses
        overlap_result = conn.execute(
            text("""
                SELECT COUNT(*) as cnt
                FROM membership_pauses
                WHERE membership_id = :membership_id
                  AND status = 'active'
                  AND NOT (pause_end_date < :new_pause_start OR pause_start_date > :new_pause_end)
            """),
            {
                "membership_id": membership_id,
                "new_pause_start": pause_start.isoformat(),
                "new_pause_end": pause_end.isoformat(),
            },
        )
        overlap_count = overlap_result.scalar()
        
        if overlap_count > 0:
            raise HTTPException(status_code=409, detail="Pause period overlaps with an existing active pause")

        # Insert new pause
        insert_result = conn.execute(
            text("""
                INSERT INTO membership_pauses (membership_id, pause_start_date, pause_end_date, reason, status)
                VALUES (:membership_id, :pause_start_date, :pause_end_date, :reason, :status)
                RETURNING id, membership_id, pause_start_date, pause_end_date, status
            """),
            {
                "membership_id": membership_id,
                "pause_start_date": pause_start.isoformat(),
                "pause_end_date": pause_end.isoformat(),
                "reason": None,
                "status": "active",
            },
        )
        new_pause_row = insert_result.fetchone()
        
        # Update membership status to paused
        conn.execute(
            text("""
                UPDATE memberships
                SET status = 'paused', updated_at = CURRENT_TIMESTAMP
                WHERE id = :membership_id
            """),
            {"membership_id": membership_id},
        )

        return PauseResponse(
            id=new_pause_row.id,
            membership_id=new_pause_row.membership_id,
            pause_start_date=str(new_pause_row.pause_start_date),
            pause_end_date=str(new_pause_row.pause_end_date),
            status=new_pause_row.status,
        )
