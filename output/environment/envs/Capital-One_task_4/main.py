import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class TransferRequest(BaseModel):
    from_account_last4: str = Field(..., description="Last 4 digits of the source account number")
    to_account_last4: str = Field(..., description="Last 4 digits of the destination account number")
    amount: float = Field(..., gt=0, description="Amount to transfer in USD")
    memo: Optional[str] = Field(None, description="Optional memo for the transfer")


class TransferResponse(BaseModel):
    transfer_id: int
    status: str
    message: str


@app.post("/transfer", response_model=TransferResponse, operation_id="initiate_transfer")
def initiate_transfer(request: TransferRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.begin() as conn:
        # Find the source account owned by the current user
        stmt_from = text("""
            SELECT id, balance 
            FROM accounts 
            WHERE user_id = :user_id AND account_number_last4 = :last4
        """)
        res_from = conn.execute(stmt_from, {"user_id": current_user_id, "last4": request.from_account_last4}).fetchone()
        
        if not res_from:
            raise HTTPException(status_code=404, detail="Source account not found or does not belong to user")
        
        from_account_id, from_balance = res_from
        
        # Check sufficient balance
        if from_balance < request.amount:
            raise HTTPException(status_code=400, detail="Insufficient funds in source account")

        # Find the destination account owned by the current user
        stmt_to = text("""
            SELECT id 
            FROM accounts 
            WHERE user_id = :user_id AND account_number_last4 = :last4
        """)
        res_to = conn.execute(stmt_to, {"user_id": current_user_id, "last4": request.to_account_last4}).fetchone()
        
        if not res_to:
            raise HTTPException(status_code=404, detail="Destination account not found or does not belong to user")
        
        to_account_id = res_to[0]

        # Ensure different accounts
        if from_account_id == to_account_id:
            raise HTTPException(status_code=400, detail="Cannot transfer to the same account")

        # Deduct from source account
        update_from_stmt = text("""
            UPDATE accounts 
            SET balance = balance - :amount, updated_at = CURRENT_TIMESTAMP 
            WHERE id = :account_id
        """)
        conn.execute(update_from_stmt, {"amount": request.amount, "account_id": from_account_id})

        # Add to destination account
        update_to_stmt = text("""
            UPDATE accounts 
            SET balance = balance + :amount, updated_at = CURRENT_TIMESTAMP 
            WHERE id = :account_id
        """)
        conn.execute(update_to_stmt, {"amount": request.amount, "account_id": to_account_id})

        # Insert transfer record
        insert_transfer_stmt = text("""
            INSERT INTO transfers (user_id, from_account_id, to_account_id, amount, memo, status, created_at, updated_at)
            VALUES (:user_id, :from_account_id, :to_account_id, :amount, :memo, 'completed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        result = conn.execute(insert_transfer_stmt, {
            "user_id": current_user_id,
            "from_account_id": from_account_id,
            "to_account_id": to_account_id,
            "amount": request.amount,
            "memo": request.memo
        })
        
        transfer_id = result.lastrowid

    return TransferResponse(
        transfer_id=transfer_id,
        status="completed",
        message=f"Successfully transferred ${request.amount:.2f} from account ending in {request.from_account_last4} to account ending in {request.to_account_last4}"
    )
