import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from typing import Optional, Literal

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AutopaySetupRequest(BaseModel):
    credit_card_account_id: int
    funding_account_id: int


class ScheduledPaymentRequest(BaseModel):
    source_account_id: int
    destination_account_id: int
    amount: float
    payment_date: str  # YYYY-MM-DD
    memo: Optional[str] = None


def get_current_user_id():
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.post("/autopay/setups", operation_id="create_autopay_setup")
def create_autopay_setup(
    payload: AutopaySetupRequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Create an autopay setup for a credit card.
    Sets up automatic monthly payments from a checking account to a credit card 
    on the due date, covering the full statement balance.
    """
    
    # Verify that the accounts belong to the current user and have correct types
    with engine.connect() as conn:
        # Check credit card account
        cc_result = conn.execute(
            text("""
                SELECT id, account_type, user_id 
                FROM accounts 
                WHERE id = :cc_id AND user_id = :uid
            """),
            {"cc_id": payload.credit_card_account_id, "uid": user_id}
        ).fetchone()
        
        if not cc_result:
            raise HTTPException(status_code=404, detail="Credit card account not found or does not belong to user")
        if cc_result.account_type != 'credit_card':
            raise HTTPException(status_code=400, detail="Account specified as credit_card is not of type credit_card")

        # Check funding account
        fa_result = conn.execute(
            text("""
                SELECT id, account_type, user_id 
                FROM accounts 
                WHERE id = :fa_id AND user_id = :uid
            """),
            {"fa_id": payload.funding_account_id, "uid": user_id}
        ).fetchone()
        
        if not fa_result:
            raise HTTPException(status_code=404, detail="Funding account not found or does not belong to user")
        if fa_result.account_type != 'checking':
            raise HTTPException(status_code=400, detail="Account specified as funding is not of type checking")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Insert into autopay_setups
        # payment_amount_type: 'statement_balance' (pay in full)
        # payment_date_type: 'due_date'
        insert_stmt = text("""
            INSERT INTO autopay_setups (
                user_id, 
                credit_card_account_id, 
                funding_account_id, 
                payment_amount_type, 
                fixed_amount, 
                payment_date_type, 
                specific_day, 
                is_active, 
                created_at, 
                updated_at
            ) VALUES (
                :user_id, 
                :cc_id, 
                :fa_id, 
                'statement_balance', 
                NULL, 
                'due_date', 
                NULL, 
                1, 
                :now, 
                :now
            ) RETURNING *
        """)
        
        result = conn.execute(
            insert_stmt,
            {
                "user_id": user_id,
                "cc_id": payload.credit_card_account_id,
                "fa_id": payload.funding_account_id,
                "now": now
            }
        ).fetchone()
        
        conn.commit()

        return dict(result._mapping)


@app.post("/scheduled-payments", operation_id="create_scheduled_payment")
def create_scheduled_payment(
    payload: ScheduledPaymentRequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Schedule a one-time extra payment.
    Schedules a one-time additional payment from the checking account to the credit card.
    """
    
    # Validate amount
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    # Verify that the accounts belong to the current user and have correct types
    with engine.connect() as conn:
        # Check source account (should be checking)
        src_result = conn.execute(
            text("""
                SELECT id, account_type, user_id 
                FROM accounts 
                WHERE id = :src_id AND user_id = :uid
            """),
            {"src_id": payload.source_account_id, "uid": user_id}
        ).fetchone()
        
        if not src_result:
            raise HTTPException(status_code=404, detail="Source account not found or does not belong to user")
        if src_result.account_type != 'checking':
            raise HTTPException(status_code=400, detail="Source account is not of type checking")

        # Check destination account (should be credit_card)
        dest_result = conn.execute(
            text("""
                SELECT id, account_type, user_id 
                FROM accounts 
                WHERE id = :dest_id AND user_id = :uid
            """),
            {"dest_id": payload.destination_account_id, "uid": user_id}
        ).fetchone()
        
        if not dest_result:
            raise HTTPException(status_code=404, detail="Destination account not found or does not belong to user")
        if dest_result.account_type != 'credit_card':
            raise HTTPException(status_code=400, detail="Destination account is not of type credit_card")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Insert into scheduled_payments
        insert_stmt = text("""
            INSERT INTO scheduled_payments (
                user_id, 
                source_account_id, 
                destination_account_id, 
                amount, 
                payment_date, 
                status, 
                memo, 
                created_at, 
                updated_at
            ) VALUES (
                :user_id, 
                :src_id, 
                :dest_id, 
                :amount, 
                :payment_date, 
                'scheduled', 
                :memo, 
                :now, 
                :now
            ) RETURNING *
        """)
        
        result = conn.execute(
            insert_stmt,
            {
                "user_id": user_id,
                "src_id": payload.source_account_id,
                "dest_id": payload.destination_account_id,
                "amount": payload.amount,
                "payment_date": payload.payment_date,
                "memo": payload.memo,
                "now": now
            }
        ).fetchone()
        
        conn.commit()

        return dict(result._mapping)
