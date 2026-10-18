import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AddPaymentMethodRequest(BaseModel):
    card_number: str
    expiry: str
    cvv: str
    set_as_default_for_subscription: bool = False


class PaymentMethodResponse(BaseModel):
    id: int
    user_id: int
    card_number: str
    expiry: str
    is_default: int
    created_at: str


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")


@app.post("/payment-methods", response_model=PaymentMethodResponse, operation_id="add_payment_method")
def add_payment_method(payload: AddPaymentMethodRequest):
    user_id = get_current_user_id()

    with engine.begin() as conn:
        # Check if user has any existing default payment method
        result = conn.execute(
            text("SELECT COUNT(*) FROM payment_methods WHERE user_id = :uid AND is_default = 1"),
            {"uid": user_id}
        )
        has_existing_default = result.scalar() > 0

        # Determine if this new payment method should be default
        # If user has no existing default, this one becomes default automatically per spec description
        # Also check if explicitly requested to set as default for subscription
        is_default = 0
        
        if not has_existing_default:
            is_default = 1
        elif payload.set_as_default_for_subscription:
            # We will handle setting it as default after insertion if needed, 
            # but the 'is_default' flag on the payment_method itself might need updating.
            # The spec says "If the user has no existing default payment method, this one will be set as default."
            # It doesn't say we force is_default=1 just because they want it for subscription if another exists.
            # However, usually "set as default" implies making it the primary one.
            # Let's look at the PUT endpoint behavior. The PUT endpoint sets a specific PM as default.
            # For POST, if set_as_default_for_subscription is true, does it change the is_default flag?
            # The response includes is_default.
            # Logic: 
            # 1. Insert the row. Initially is_default depends on whether there was an existing default.
            # 2. If set_as_default_for_subscription is True, we likely need to update the subscription and potentially the is_default flags.
            
            # Re-reading carefully: "Creates a new payment method record... If the user has no existing default payment method, this one will be set as default."
            # This suggests automatic defaulting only happens when none exist.
            # "Optionally set as default for the user's subscription."
            # This part refers to linking the subscription.default_payment_method_id.
            
            pass 

        # Calculate initial is_default based on existence of other defaults
        if not has_existing_default:
            is_default = 1
        else:
            is_default = 0

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_result = conn.execute(
            text("""
                INSERT INTO payment_methods (user_id, card_number, expiry, cvv, is_default, created_at, updated_at)
                VALUES (:uid, :cn, :exp, :cvv, :isd, :now, :now)
            """),
            {
                "uid": user_id,
                "cn": payload.card_number,
                "exp": payload.expiry,
                "cvv": payload.cvv,
                "isd": is_default,
                "now": now
            }
        )
        pm_id = insert_result.lastrowid

        # Handle setting as default for subscription
        if payload.set_as_default_for_subscription:
            # Find the user's active subscription
            sub_result = conn.execute(
                text("SELECT id FROM subscriptions WHERE user_id = :uid LIMIT 1"),
                {"uid": user_id}
            )
            sub_row = sub_result.fetchone()
            
            if sub_row:
                sub_id = sub_row[0]
                
                # Update subscription to point to this new payment method
                conn.execute(
                    text("UPDATE subscriptions SET default_payment_method_id = :pm_id, updated_at = :now WHERE id = :sid"),
                    {"pm_id": pm_id, "sid": sub_id, "now": now}
                )
                
                # Should we also flip the is_default flag on the payment methods?
                # Typically, yes. Unset others, set this one.
                # But wait, if has_existing_default was true, we inserted with is_default=0.
                # Now we are making it the default for the subscription.
                # Does "default for subscription" imply "is_default" in payment_methods table?
                # Looking at the schema, `subscriptions` has `default_payment_method_id`.
                # `payment_methods` has `is_default`.
                # Usually these are kept in sync.
                # If I set it as default for subscription, I should probably mark it as default in the PM table too.
                
                # Clear other defaults for this user
                conn.execute(
                    text("UPDATE payment_methods SET is_default = 0, updated_at = :now WHERE user_id = :uid AND id != :pm_id"),
                    {"uid": user_id, "pm_id": pm_id, "now": now}
                )
                # Set this one as default
                conn.execute(
                    text("UPDATE payment_methods SET is_default = 1, updated_at = :now WHERE id = :pm_id"),
                    {"pm_id": pm_id, "now": now}
                )
                # Update local var for response
                is_default = 1

        # Fetch the final state of the payment method
        fetch_result = conn.execute(
            text("SELECT id, user_id, card_number, expiry, is_default, created_at FROM payment_methods WHERE id = :pid"),
            {"pid": pm_id}
        )
        row = fetch_result.fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created payment method")

        return PaymentMethodResponse(
            id=row[0],
            user_id=row[1],
            card_number=row[2],
            expiry=row[3],
            is_default=row[4],
            created_at=str(row[5])
        )


@app.put("/payment-methods/{id}/set-default", response_model=PaymentMethodResponse, operation_id="set_default_payment_method")
def set_default_payment_method(id: int):
    user_id = get_current_user_id()

    with engine.begin() as conn:
        # Verify ownership
        owner_check = conn.execute(
            text("SELECT user_id FROM payment_methods WHERE id = :pid"),
            {"pid": id}
        ).fetchone()
        
        if not owner_check:
            raise HTTPException(status_code=404, detail="Payment method not found")
        
        if owner_check[0] != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to modify this payment method")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Unset all other defaults for this user
        conn.execute(
            text("UPDATE payment_methods SET is_default = 0, updated_at = :now WHERE user_id = :uid AND id != :pid"),
            {"uid": user_id, "pid": id, "now": now}
        )

        # Set this one as default
        conn.execute(
            text("UPDATE payment_methods SET is_default = 1, updated_at = :now WHERE id = :pid"),
            {"pid": id, "now": now}
        )

        # Update subscription if exists
        sub_result = conn.execute(
            text("SELECT id FROM subscriptions WHERE user_id = :uid LIMIT 1"),
            {"uid": user_id}
        )
        sub_row = sub_result.fetchone()
        if sub_row:
            sub_id = sub_row[0]
            conn.execute(
                text("UPDATE subscriptions SET default_payment_method_id = :pid, updated_at = :now WHERE id = :sid"),
                {"pid": id, "sid": sub_id, "now": now}
            )

        # Fetch updated record
        fetch_result = conn.execute(
            text("SELECT id, user_id, card_number, expiry, is_default, created_at FROM payment_methods WHERE id = :pid"),
            {"pid": id}
        )
        row = fetch_result.fetchone()

        return PaymentMethodResponse(
            id=row[0],
            user_id=row[1],
            card_number=row[2],
            expiry=row[3],
            is_default=row[4],
            created_at=str(row[5])
        )
