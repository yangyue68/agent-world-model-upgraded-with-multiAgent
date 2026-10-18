import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class SubscriptionResponse(BaseModel):
    id: int
    user_id: int
    plan_id: int
    billing_interval_id: int
    status: str
    started_at: datetime
    current_period_start: datetime
    current_period_end: Optional[datetime]
    cancelled_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class UserSubscriptionResponse(BaseModel):
    id: int
    user_id: int
    plan_id: int
    plan_name: str
    billing_interval_id: int
    billing_interval_name: str
    status: str
    started_at: datetime
    current_period_start: datetime
    current_period_end: Optional[datetime]
    cancelled_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class UpdateBillingIntervalRequest(BaseModel):
    billing_interval_id: int


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.put("/subscriptions/{subscription_id}", response_model=SubscriptionResponse, operation_id="update_subscription_billing_interval")
def update_subscription_billing_interval(
    subscription_id: int,
    request: UpdateBillingIntervalRequest,
    db: SessionLocal = Depends(get_db)
):
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Fetch the subscription and verify ownership
    query_sub = text("""
        SELECT s.*, p.name as plan_name 
        FROM subscriptions s
        JOIN plans p ON s.plan_id = p.id
        WHERE s.id = :sub_id AND s.user_id = :user_id
    """)
    result = db.execute(query_sub, {"sub_id": subscription_id, "user_id": current_user_id}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Subscription not found or does not belong to user")

    sub_data = dict(result._mapping)
    
    # Check if subscription is active
    if sub_data["status"] != "active":
        raise HTTPException(status_code=400, detail="Only active subscriptions can be modified")

    # Check if plan is Pro (plan_id 2 based on schema data, but better to check name)
    if sub_data["plan_name"] != "Pro":
        raise HTTPException(status_code=400, detail="Billing interval change only allowed for Pro plan")

    # Validate new billing interval exists and is either monthly (1) or annual (2)
    target_interval_id = request.billing_interval_id
    if target_interval_id not in [1, 2]:
        raise HTTPException(status_code=400, detail="Invalid billing interval. Must be 1 (monthly) or 2 (annual)")

    # Check if it's actually a change
    if sub_data["billing_interval_id"] == target_interval_id:
        # No change needed, return current state
        pass
    else:
        # Perform update
        now = datetime.utcnow()
        
        # Calculate new period end based on new interval
        # We need to know the months for the new interval
        query_interval = text("SELECT months FROM billing_intervals WHERE id = :interval_id")
        interval_res = db.execute(query_interval, {"interval_id": target_interval_id}).fetchone()
        if not interval_res:
            raise HTTPException(status_code=400, detail="Target billing interval not found")
        
        months = interval_res.months
        
        # New period start is usually now or next cycle? 
        # The spec says "Change the billing interval". Usually this takes effect immediately or at renewal.
        # Given the simplicity, let's assume immediate switch for the current period calculation logic isn't strictly defined 
        # other than updating the field. However, standard behavior might keep current_period_end until renewal.
        # But looking at the task "Change my subscription billing interval", we just update the FK.
        # Let's stick to updating the billing_interval_id and updated_at.
        # If we were to recalculate periods, it would be complex. The prompt doesn't explicitly ask to recalculate dates,
        # just to change the interval. I will update the interval and timestamp.
        
        update_query = text("""
            UPDATE subscriptions
            SET billing_interval_id = :new_interval_id,
                updated_at = :now
            WHERE id = :sub_id
        """)
        db.execute(update_query, {
            "new_interval_id": target_interval_id,
            "now": now,
            "sub_id": subscription_id
        })
        db.commit()

    # Fetch updated subscription
    final_query = text("""
        SELECT * FROM subscriptions WHERE id = :sub_id
    """)
    final_result = db.execute(final_query, {"sub_id": subscription_id}).fetchone()
    
    if not final_result:
        raise HTTPException(status_code=500, detail="Failed to retrieve updated subscription")

    row = dict(final_result._mapping)
    
    return SubscriptionResponse(**row)


@app.get("/subscriptions", response_model=list[UserSubscriptionResponse], operation_id="get_user_subscriptions")
def get_user_subscriptions(db: SessionLocal = Depends(get_db)):
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Retrieve all active subscriptions for the current user with plan and interval details
    query = text("""
        SELECT 
            s.id,
            s.user_id,
            s.plan_id,
            p.name as plan_name,
            s.billing_interval_id,
            bi.name as billing_interval_name,
            s.status,
            s.started_at,
            s.current_period_start,
            s.current_period_end,
            s.cancelled_at,
            s.created_at,
            s.updated_at
        FROM subscriptions s
        JOIN plans p ON s.plan_id = p.id
        JOIN billing_intervals bi ON s.billing_interval_id = bi.id
        WHERE s.user_id = :user_id AND s.status = 'active'
    """)
    
    results = db.execute(query, {"user_id": current_user_id}).fetchall()
    
    response_list = []
    for r in results:
        response_list.append(UserSubscriptionResponse(**dict(r._mapping)))
        
    return response_list
