import os
from datetime import datetime, timedelta
import calendar
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
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
    status: str
    current_period_start: str
    current_period_end: str


class CouponApplicationResponse(BaseModel):
    id: int
    subscription_id: int
    coupon_id: int
    applied_at: str
    billing_cycle_start: str
    billing_cycle_end: str


def get_current_user_id() -> int:
    """Retrieve the current logged-in user ID from the environment."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.put("/subscriptions/{subscription_id}/upgrade", response_model=SubscriptionResponse, operation_id="upgrade_subscription")
def upgrade_subscription(subscription_id: int):
    """
    Upgrades the specified subscription from the Basic plan to the Premium plan.
    This operation updates the plan_id in the subscriptions table and applies the new pricing effective immediately.
    """
    current_user_id = get_current_user_id()

    with SessionLocal() as db:
        # Fetch the subscription and verify ownership
        sub_query = text("""
            SELECT s.id, s.user_id, s.plan_id, s.status, s.current_period_start, s.current_period_end, p.name as plan_name
            FROM subscriptions s
            JOIN plans p ON s.plan_id = p.id
            WHERE s.id = :sub_id AND s.user_id = :user_id
        """)
        result = db.execute(sub_query, {"sub_id": subscription_id, "user_id": current_user_id})
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Subscription not found or access denied")

        sub_id, user_id, current_plan_id, status, period_start, period_end, plan_name = row

        # Verify current plan is 'Basic'
        if plan_name != "Basic":
            raise HTTPException(status_code=400, detail="Only subscriptions on the 'Basic' plan can be upgraded via this endpoint")

        # Find the 'Premium' plan ID
        premium_plan_query = text("SELECT id FROM plans WHERE name = 'Premium' LIMIT 1")
        premium_result = db.execute(premium_plan_query)
        premium_row = premium_result.fetchone()

        if not premium_row:
            raise HTTPException(status_code=500, detail="Premium plan configuration missing")

        premium_plan_id = premium_row[0]

        # Update the subscription
        update_query = text("""
            UPDATE subscriptions
            SET plan_id = :new_plan_id, updated_at = CURRENT_TIMESTAMP
            WHERE id = :sub_id
        """)
        db.execute(update_query, {"new_plan_id": premium_plan_id, "sub_id": sub_id})
        db.commit()

        # Return updated data
        return SubscriptionResponse(
            id=sub_id,
            user_id=user_id,
            plan_id=premium_plan_id,
            status=status,
            current_period_start=str(period_start),
            current_period_end=str(period_end)
        )


@app.post("/subscriptions/{subscription_id}/apply-coupon", response_model=CouponApplicationResponse, operation_id="apply_coupon_to_subscription")
def apply_coupon_to_subscription(subscription_id: int, coupon_code: str = Query(...)):
    """
    Applies the given coupon code to the specified subscription for the upcoming billing cycle.
    Validates that the coupon is active and not expired, and records the application in the subscription_coupons table.
    """
    current_user_id = get_current_user_id()

    with SessionLocal() as db:
        # Fetch the subscription details including plan interval
        detailed_sub_query = text("""
            SELECT s.id, s.user_id, s.current_period_start, s.current_period_end, p.billing_interval
            FROM subscriptions s
            JOIN plans p ON s.plan_id = p.id
            WHERE s.id = :sub_id AND s.user_id = :user_id
        """)
        det_result = db.execute(detailed_sub_query, {"sub_id": subscription_id, "user_id": current_user_id})
        det_row = det_result.fetchone()
        
        if not det_row:
             raise HTTPException(status_code=404, detail="Subscription not found or access denied")
             
        sub_id, user_id, curr_period_start, curr_period_end, billing_interval = det_row
        
        # Determine next billing cycle dates
        # The task says "next billing cycle". Usually means starting after current_period_end.
        # We need to calculate the end date of the next cycle based on the interval.
        
        try:
            # Parse current period end
            cpe_dt = datetime.fromisoformat(str(curr_period_end).replace('Z', '+00:00'))
            
            if billing_interval == 'monthly':
                # Next start = Current End
                next_start = cpe_dt
                
                # Calculate next end by adding 1 month
                year = next_start.year
                month = next_start.month + 1
                day = next_start.day
                
                if month > 12:
                    month -= 12
                    year += 1
                
                # Adjust day if necessary for shorter months
                last_day_of_month = calendar.monthrange(year, month)[1]
                if day > last_day_of_month:
                    day = last_day_of_month
                    
                next_end = next_start.replace(year=year, month=month, day=day)
                
            elif billing_interval == 'yearly':
                next_start = cpe_dt
                next_end = next_start.replace(year=cpe_dt.year + 1)
            else:
                raise HTTPException(status_code=400, detail="Unknown billing interval")

        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error calculating billing cycle: {str(e)}")

        # Validate Coupon
        coupon_query = text("""
            SELECT id, discount_percent, valid_from, valid_until, max_uses, times_used
            FROM coupons
            WHERE code = :code
        """)
        cp_res = db.execute(coupon_query, {"code": coupon_code})
        cp_row = cp_res.fetchone()

        if not cp_row:
            raise HTTPException(status_code=404, detail="Coupon not found")

        c_id, disc_pct, v_from, v_until, max_u, t_used = cp_row

        now = datetime.utcnow()
        
        # Check validity window
        if v_from:
            vf_dt = datetime.fromisoformat(str(v_from).replace('Z', '+00:00'))
            if now < vf_dt:
                 raise HTTPException(status_code=400, detail="Coupon is not yet valid")
                 
        if v_until:
            vu_dt = datetime.fromisoformat(str(v_until).replace('Z', '+00:00'))
            if now > vu_dt:
                 raise HTTPException(status_code=400, detail="Coupon has expired")

        # Check usage limits
        if max_u is not None and t_used >= max_u:
            raise HTTPException(status_code=400, detail="Coupon usage limit reached")

        # Format datetimes for SQLite storage consistency (usually space separated ISO)
        bcs_str = next_start.strftime('%Y-%m-%d %H:%M:%S')
        bce_str = next_end.strftime('%Y-%m-%d %H:%M:%S')
        now_str = now.strftime('%Y-%m-%d %H:%M:%S')

        # Check if already applied for this specific billing cycle start
        check_existing = text("""
            SELECT id FROM subscription_coupons
            WHERE subscription_id = :sub_id AND coupon_id = :c_id AND billing_cycle_start = :bcs
        """)
        ex_res = db.execute(check_existing, {"sub_id": sub_id, "c_id": c_id, "bcs": bcs_str})
        if ex_res.fetchone():
            raise HTTPException(status_code=409, detail="Coupon already applied for this billing cycle")

        # Insert into subscription_coupons
        insert_sc = text("""
            INSERT INTO subscription_coupons (subscription_id, coupon_id, applied_at, billing_cycle_start, billing_cycle_end, created_at, updated_at)
            VALUES (:sub_id, :c_id, :now, :bcs, :bce, :now, :now)
        """)
        
        res_insert = db.execute(insert_sc, {
            "sub_id": sub_id,
            "c_id": c_id,
            "now": now_str,
            "bcs": bcs_str,
            "bce": bce_str
        })
        
        sc_id = res_insert.lastrowid

        # Increment times_used in coupons table
        update_cp = text("""
            UPDATE coupons
            SET times_used = times_used + 1, updated_at = :now
            WHERE id = :c_id
        """)
        db.execute(update_cp, {"c_id": c_id, "now": now_str})
        
        db.commit()

        return CouponApplicationResponse(
            id=sc_id,
            subscription_id=sub_id,
            coupon_id=c_id,
            applied_at=now_str,
            billing_cycle_start=bcs_str,
            billing_cycle_end=bce_str
        )
