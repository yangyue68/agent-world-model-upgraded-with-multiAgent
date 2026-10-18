import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AccountResponse(BaseModel):
    id: int
    user_id: int
    account_type: str
    account_number_last4: str
    balance: float
    currency: str
    status: str


class TransferRequest(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: float
    memo: str | None = None


class TransferResponse(BaseModel):
    id: int
    user_id: int
    from_account_id: int
    to_account_id: int
    amount: float
    transfer_type: str
    status: str
    memo: str | None = None
    created_at: str
    updated_at: str


def get_current_user_id() -> int:
    uid = os.environ.get("AWM_CURRENT_USER_ID")
    if not uid:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        return int(uid)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")


@app.get("/accounts", response_model=list[AccountResponse], operation_id="find_accounts")
def find_accounts(
    account_type: str | None = Query(None),
    account_number_last4: str | None = Query(None),
):
    user_id = get_current_user_id()
    query = "SELECT id, user_id, account_type, account_number_last4, balance, currency, status FROM accounts WHERE user_id = :user_id AND status = 'active'"
    params = {"user_id": user_id}

    if account_type is not None:
        query += " AND account_type = :account_type"
        params["account_type"] = account_type

    if account_number_last4 is not None:
        query += " AND account_number_last4 = :account_number_last4"
        params["account_number_last4"] = account_number_last4

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        rows = result.fetchall()

    accounts = []
    for row in rows:
        accounts.append(AccountResponse(
            id=row.id,
            user_id=row.user_id,
            account_type=row.account_type,
            account_number_last4=row.account_number_last4,
            balance=float(row.balance),
            currency=row.currency,
            status=row.status,
        ))
    return accounts


@app.post("/transfers", response_model=TransferResponse, operation_id="create_transfer")
def create_transfer(req: TransferRequest):
    user_id = get_current_user_id()

    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    # Validate source account
    with engine.connect() as conn:
        src_result = conn.execute(
            text("SELECT id, user_id, account_type, balance, status FROM accounts WHERE id = :id"),
            {"id": req.from_account_id},
        ).fetchone()

    if src_result is None:
        raise HTTPException(status_code=404, detail="Source account not found")
    if src_result.user_id != user_id:
        raise HTTPException(status_code=403, detail="Source account does not belong to current user")
    if src_result.status != "active":
        raise HTTPException(status_code=400, detail="Source account is not active")
    if float(src_result.balance) < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient funds in source account")

    # Validate destination account
    with engine.connect() as conn:
        dst_result = conn.execute(
            text("SELECT id, user_id, account_type, status FROM accounts WHERE id = :id"),
            {"id": req.to_account_id},
        ).fetchone()

    if dst_result is None:
        raise HTTPException(status_code=404, detail="Destination account not found")
    if dst_result.user_id != user_id:
        raise HTTPException(status_code=403, detail="Destination account does not belong to current user")
    if dst_result.status != "active":
        raise HTTPException(status_code=400, detail="Destination account is not active")

    if req.from_account_id == req.to_account_id:
        raise HTTPException(status_code=400, detail="Source and destination accounts must be different")

    now = datetime.now(timezone.utc).isoformat()

    with engine.begin() as conn:
        # Insert transfer record
        insert_result = conn.execute(
            text("""
                INSERT INTO transfers (user_id, from_account_id, to_account_id, amount, transfer_type, status, memo, created_at, updated_at)
                VALUES (:user_id, :from_account_id, :to_account_id, :amount, :transfer_type, :status, :memo, :created_at, :updated_at)
            """),
            {
                "user_id": user_id,
                "from_account_id": req.from_account_id,
                "to_account_id": req.to_account_id,
                "amount": req.amount,
                "transfer_type": "immediate",
                "status": "completed",
                "memo": req.memo,
                "created_at": now,
                "updated_at": now,
            },
        )
        transfer_id = insert_result.lastrowid

        # Update balances
        conn.execute(
            text("UPDATE accounts SET balance = balance - :amount, updated_at = :now WHERE id = :id"),
            {"amount": req.amount, "now": now, "id": req.from_account_id},
        )
        conn.execute(
            text("UPDATE accounts SET balance = balance + :amount, updated_at = :now WHERE id = :id"),
            {"amount": req.amount, "now": now, "id": req.to_account_id},
        )

        # Fetch the created transfer
        t_row = conn.execute(
            text("SELECT id, user_id, from_account_id, to_account_id, amount, transfer_type, status, memo, created_at, updated_at FROM transfers WHERE id = :id"),
            {"id": transfer_id},
        ).fetchone()

    return TransferResponse(
        id=t_row.id,
        user_id=t_row.user_id,
        from_account_id=t_row.from_account_id,
        to_account_id=t_row.to_account_id,
        amount=float(t_row.amount),
        transfer_type=t_row.transfer_type,
        status=t_row.status,
        memo=t_row.memo,
        created_at=str(t_row.created_at),
        updated_at=str(t_row.updated_at),
    )
