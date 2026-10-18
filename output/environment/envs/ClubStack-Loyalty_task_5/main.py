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


# --- Pydantic Models ---

class UserResponse(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None


class ClubResponse(BaseModel):
    id: int
    name: str


class SubscriptionResponse(BaseModel):
    id: int
    user_id: int
    club_id: int
    status: str
    start_date: str
    end_date: Optional[str] = None


class PauseRequest(BaseModel):
    subscription_id: int
    pause_start_date: str
    pause_end_date: str


class PauseResponse(BaseModel):
    id: int
    subscription_id: int
    pause_start_date: str
    pause_end_date: str


# --- Helper Functions ---

def get_current_user_id() -> int:
    """Retrieve the current user ID from the environment."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


def calculate_next_monday(reference_date: Optional[date] = None) -> date:
    """Calculate the date of the next Monday relative to reference_date (default today)."""
    if reference_date is None:
        reference_date = date.today()
    
    # weekday(): Monday is 0, Sunday is 6
    days_ahead = 0 - reference_date.weekday() 
    if days_ahead <= 0: # Target day already happened this week or is today
        days_ahead += 7
    
    return reference_date + timedelta(days=days_ahead)


# --- Endpoints ---

@app.get("/users/current", response_model=UserResponse, operation_id="get_current_user")
def get_current_user():
    user_id = get_current_user_id()
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, username, email, full_name FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="User not found")
            
        return UserResponse(
            id=result.id,
            username=result.username,
            email=result.email,
            full_name=result.full_name
        )


@app.get("/clubs", response_model=list[ClubResponse], operation_id="find_clubs")
def find_clubs(name: str = Query(..., description="Club name")):
    with engine.connect() as conn:
        results = conn.execute(
            text("SELECT id, name FROM clubs WHERE name LIKE :pattern ORDER BY id"),
            {"pattern": f"%{name}%"}
        ).fetchall()
        
        return [ClubResponse(id=r.id, name=r.name) for r in results]


@app.get("/subscriptions", response_model=list[SubscriptionResponse], operation_id="find_subscriptions")
def find_subscriptions():
    user_id = get_current_user_id()
    with engine.connect() as conn:
        results = conn.execute(
            text("""
                SELECT id, user_id, club_id, status, start_date, end_date 
                FROM subscriptions 
                WHERE user_id = :uid AND status != 'cancelled'
                ORDER BY id
            """),
            {"uid": user_id}
        ).fetchall()
        
        return [
            SubscriptionResponse(
                id=r.id,
                user_id=r.user_id,
                club_id=r.club_id,
                status=r.status,
                start_date=str(r.start_date),
                end_date=str(r.end_date) if r.end_date else None
            )
            for r in results
        ]


@app.post("/subscription-pauses", response_model=PauseResponse, operation_id="create_pause")
def create_pause(payload: PauseRequest):
    user_id = get_current_user_id()
    
    # Validate dates
    try:
        start_dt = date.fromisoformat(payload.pause_start_date)
        end_dt = date.fromisoformat(payload.pause_end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
        
    if end_dt < start_dt:
        raise HTTPException(status_code=400, detail="End date cannot be before start date.")

    with engine.begin() as conn:
        # Verify subscription belongs to user and exists
        sub_result = conn.execute(
            text("SELECT id, status FROM subscriptions WHERE id = :sid AND user_id = :uid"),
            {"sid": payload.subscription_id, "uid": user_id}
        ).fetchone()
        
        if not sub_result:
            raise HTTPException(status_code=404, detail="Subscription not found or does not belong to user.")
            
        if sub_result.status == 'cancelled':
             raise HTTPException(status_code=400, detail="Cannot pause a cancelled subscription.")

        # Insert pause record
        insert_result = conn.execute(
            text("""
                INSERT INTO subscription_pauses (subscription_id, pause_start_date, pause_end_date)
                VALUES (:sid, :start, :end)
                RETURNING id, subscription_id, pause_start_date, pause_end_date
            """),
            {
                "sid": payload.subscription_id,
                "start": payload.pause_start_date,
                "end": payload.pause_end_date
            }
        ).fetchone()
        
        # Update subscription status to paused
        conn.execute(
            text("UPDATE subscriptions SET status = 'paused', updated_at = CURRENT_TIMESTAMP WHERE id = :sid"),
            {"sid": payload.subscription_id}
        )
        
        return PauseResponse(
            id=insert_result.id,
            subscription_id=insert_result.subscription_id,
            pause_start_date=str(insert_result.pause_start_date),
            pause_end_date=str(insert_result.pause_end_date)
        )
