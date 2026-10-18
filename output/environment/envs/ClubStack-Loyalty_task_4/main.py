import os
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the environment/runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class CancelMembershipResponse(BaseModel):
    membership_id: int
    subscription_id: int
    status: str
    effective_cancellation_date: str  # ISO format date string


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.put("/memberships/{subscription_id}/cancel", response_model=CancelMembershipResponse, operation_id="cancel_membership")
def cancel_membership(subscription_id: int, db=Depends(get_db)):
    """
    Cancel an active membership by subscription ID.
    
    - Verifies the membership exists and belongs to the current user.
    - Verifies the membership is currently 'active'.
    - Updates status to 'cancelled', sets cancelled_at to now, and effective_cancellation_date to today.
    """
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Fetch the membership details including plan name for verification if needed, 
    # though the spec says "for the 'Gold Fitness' plan" in the task description, 
    # the API endpoint itself just takes subscription_id. The logic should ensure ownership and active status.
    query = text("""
        SELECT m.id, m.user_id, m.status, p.name as plan_name
        FROM memberships m
        JOIN plans p ON m.plan_id = p.id
        WHERE m.subscription_id = :sub_id
    """)
    
    result = db.execute(query, {"sub_id": subscription_id}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Membership not found")
    
    membership_id, user_id, status, plan_name = result
    
    # Check ownership
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You do not own this membership")
    
    # Check status
    if status != 'active':
        raise HTTPException(status_code=400, detail=f"Cannot cancel membership with status '{status}'. Only 'active' memberships can be cancelled.")

    # Perform update
    today = date.today().isoformat()
    now_dt = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
    update_query = text("""
        UPDATE memberships
        SET status = 'cancelled',
            cancelled_at = :now,
            effective_cancellation_date = :today,
            updated_at = :now
        WHERE id = :mid
    """)
    
    db.execute(update_query, {
        "now": now_dt,
        "today": today,
        "mid": membership_id
    })
    db.commit()
    
    return CancelMembershipResponse(
        membership_id=membership_id,
        subscription_id=subscription_id,
        status='cancelled',
        effective_cancellation_date=today
    )
