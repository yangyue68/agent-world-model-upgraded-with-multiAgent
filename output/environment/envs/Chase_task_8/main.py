import os
from datetime import datetime, date, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RecurringTransferCreate(BaseModel):
    from_account_id: int
    to_external_account_id: int
    amount: float
    frequency: str
    day_of_week: int
    start_date: str


class AccountResponse(BaseModel):
    id: int
    account_number_last4: str
    account_type: str
    account_name: Optional[str] = None


class ExternalAccountResponse(BaseModel):
    id: int
    account_name: str
    routing_number: str
    account_number_last4: str
    bank_name: Optional[str] = None


class RecurringTransferResponse(BaseModel):
    id: int
    user_id: int
    from_account_id: int
    to_external_account_id: int
    amount: float
    frequency: str
    day_of_week: int
    start_date: str
    status: str
    created_at: str


@app.get("/accounts", response_model=List[AccountResponse], operation_id="find_chase_checking_account")
def find_chase_checking_account(
    account_number_last4: str = Query(...),
    account_type: str = Query(...)
):
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])
    
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, account_number_last4, account_type, account_name
                FROM accounts
                WHERE user_id = :user_id
                  AND account_number_last4 = :last4
                  AND account_type = :type
            """),
            {"user_id": current_user_id, "last4": account_number_last4, "type": account_type}
        )
        rows = result.fetchall()
        
        return [
            AccountResponse(
                id=row[0],
                account_number_last4=row[1],
                account_type=row[2],
                account_name=row[3]
            )
            for row in rows
        ]


@app.get("/external_accounts", response_model=List[ExternalAccountResponse], operation_id="find_wells_fargo_account")
def find_wells_fargo_account(
    account_name: str = Query(...),
    routing_number: str = Query(...),
    is_verified: bool = Query(...)
):
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])
    
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, account_name, routing_number, account_number_last4, bank_name
                FROM external_accounts
                WHERE user_id = :user_id
                  AND account_name = :name
                  AND routing_number = :routing
                  AND is_verified = :verified
            """),
            {
                "user_id": current_user_id,
                "name": account_name,
                "routing": routing_number,
                "verified": 1 if is_verified else 0
            }
        )
        rows = result.fetchall()
        
        return [
            ExternalAccountResponse(
                id=row[0],
                account_name=row[1],
                routing_number=row[2],
                account_number_last4=row[3],
                bank_name=row[4]
            )
            for row in rows
        ]


@app.post("/recurring_transfers", response_model=RecurringTransferResponse, operation_id="create_recurring_transfer")
def create_recurring_transfer(request: RecurringTransferCreate):
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])
    
    # Validate frequency
    valid_frequencies = ['daily', 'weekly', 'biweekly', 'monthly']
    if request.frequency not in valid_frequencies:
        raise HTTPException(status_code=400, detail=f"Invalid frequency. Must be one of: {valid_frequencies}")
    
    # Validate day_of_week
    if request.day_of_week < 0 or request.day_of_week > 6:
        raise HTTPException(status_code=400, detail="day_of_week must be between 0 and 6")
    
    # Parse start_date
    try:
        start_date_obj = datetime.strptime(request.start_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date must be in YYYY-MM-DD format")
    
    # Verify source account belongs to current user
    with engine.connect() as conn:
        src_result = conn.execute(
            text("SELECT id FROM accounts WHERE id = :id AND user_id = :user_id"),
            {"id": request.from_account_id, "user_id": current_user_id}
        )
        if not src_result.fetchone():
            raise HTTPException(status_code=404, detail="Source account not found or does not belong to current user")
        
        # Verify destination external account belongs to current user
        dest_result = conn.execute(
            text("SELECT id FROM external_accounts WHERE id = :id AND user_id = :user_id"),
            {"id": request.to_external_account_id, "user_id": current_user_id}
        )
        if not dest_result.fetchone():
            raise HTTPException(status_code=404, detail="Destination external account not found or does not belong to current user")
        
        # Calculate next transfer date based on start_date and day_of_week
        # For weekly transfers, find the first occurrence of the specified day after start_date
        target_day = request.day_of_week  # 0=Sunday, 6=Saturday
        
        # Get the weekday of start_date (Monday=0, Sunday=6 in Python's weekday())
        # We need to convert to our convention: 0=Sunday, 6=Saturday
        python_weekday = start_date_obj.weekday()  # Monday=0, Sunday=6
        # Convert: Sunday=0, Monday=1, ..., Saturday=6
        current_day = (python_weekday + 1) % 7
        
        days_ahead = target_day - current_day
        if days_ahead <= 0:
            days_ahead += 7
        
        next_transfer_date = start_date_obj + timedelta(days=days_ahead)
        
        now = datetime.now().isoformat()
        
        insert_result = conn.execute(
            text("""
                INSERT INTO recurring_transfers 
                (user_id, from_account_id, to_external_account_id, amount, frequency, day_of_week, start_date, next_transfer_date, status, created_at, updated_at)
                VALUES 
                (:user_id, :from_acc, :to_ext, :amount, :freq, :dow, :start, :next, :status, :created, :updated)
            """),
            {
                "user_id": current_user_id,
                "from_acc": request.from_account_id,
                "to_ext": request.to_external_account_id,
                "amount": request.amount,
                "freq": request.frequency,
                "dow": request.day_of_week,
                "start": request.start_date,
                "next": next_transfer_date.isoformat(),
                "status": "active",
                "created": now,
                "updated": now
            }
        )
        conn.commit()
        
        new_id = insert_result.lastrowid
        
        # Fetch the created record
        fetch_result = conn.execute(
            text("""
                SELECT id, user_id, from_account_id, to_external_account_id, amount, frequency, day_of_week, start_date, status, created_at
                FROM recurring_transfers
                WHERE id = :id
            """),
            {"id": new_id}
        )
        row = fetch_result.fetchone()
        
        return RecurringTransferResponse(
            id=row[0],
            user_id=row[1],
            from_account_id=row[2],
            to_external_account_id=row[3],
            amount=row[4],
            frequency=row[5],
            day_of_week=row[6],
            start_date=str(row[7]),
            status=row[8],
            created_at=str(row[9])
        )
