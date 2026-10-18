import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CancelSubscriptionResponse(BaseModel):
    id: int
    user_id: int
    plan_id: int
    status: str
    cancelled_at: str
    cancellation_reason: str


@app.put(
    "/subscriptions/{subscription_id}/cancel",
    response_model=CancelSubscriptionResponse,
    operation_id="cancel_subscription",
    summary="Cancel a user's subscription with a reason",
)
def cancel_subscription(
    subscription_id: int = Path(..., description="Internal ID of the subscription to cancel"),
    reason: str = Body(..., embed=True, description="Reason for cancelling the subscription"),
):
    # Get current user ID from environment variable injected by runtime
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with engine.connect() as conn:
        # Fetch the subscription to verify ownership and existence
        stmt_select = text("""
            SELECT s.id, s.user_id, s.plan_id, s.status, p.name as plan_name
            FROM subscriptions s
            JOIN plans p ON s.plan_id = p.id
            WHERE s.id = :sub_id
        """)
        result = conn.execute(stmt_select, {"sub_id": subscription_id})
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Subscription not found")

        sub_user_id = row[1]
        sub_status = row[3]
        plan_name = row[4]

        # Verify ownership
        if sub_user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Forbidden: You can only cancel your own subscription")

        # Check if already cancelled or expired
        if sub_status == "cancelled":
            raise HTTPException(status_code=400, detail="Subscription is already cancelled")
        
        if sub_status == "expired":
            raise HTTPException(status_code=400, detail="Cannot cancel an expired subscription")

        # Prepare update data
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Update the subscription
        stmt_update = text("""
            UPDATE subscriptions
            SET status = 'cancelled',
                cancelled_at = :cancelled_at,
                cancellation_reason = :reason,
                updated_at = :updated_at
            WHERE id = :sub_id AND user_id = :user_id
        """)
        
        conn.execute(
            stmt_update,
            {
                "cancelled_at": now_utc,
                "reason": reason,
                "updated_at": now_utc,
                "sub_id": subscription_id,
                "user_id": current_user_id,
            }
        )
        conn.commit()

        # Fetch the updated record to return
        stmt_fetch_updated = text("""
            SELECT id, user_id, plan_id, status, cancelled_at, cancellation_reason
            FROM subscriptions
            WHERE id = :sub_id
        """)
        updated_result = conn.execute(stmt_fetch_updated, {"sub_id": subscription_id})
        updated_row = updated_result.fetchone()

        if not updated_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated subscription")

        return CancelSubscriptionResponse(
            id=updated_row[0],
            user_id=updated_row[1],
            plan_id=updated_row[2],
            status=updated_row[3],
            cancelled_at=updated_row[4],
            cancellation_reason=updated_row[5],
        )
