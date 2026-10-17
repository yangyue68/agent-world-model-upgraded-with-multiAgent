import os
from datetime import date, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class OpenAccountRequest(BaseModel):
    initial_deposit: float


class SetNicknameRequest(BaseModel):
    nickname: str


class ScheduleTransferRequest(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: float
    day_of_month: int


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")


@app.post("/accounts", operation_id="open_performance_savings_account")
def open_performance_savings_account(request: OpenAccountRequest):
    user_id = get_current_user_id()

    # Validate initial deposit
    if request.initial_deposit < 0:
        raise HTTPException(status_code=400, detail="Initial deposit cannot be negative")

    with engine.begin() as conn:
        # Generate a random last4 for the new account number
        # In a real system this would come from a sequence or external service
        # Here we just pick something unique-ish based on current max id + offset
        result = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM accounts"))
        max_id = result.scalar()
        
        # Simple deterministic generation for simulation purposes
        # Ensure it's 4 digits string
        last4 = f"{(max_id + 1000) % 10000:04d}"
        
        # Check if this last4 already exists for any active account (simple collision avoidance)
        check_result = conn.execute(
            text("SELECT COUNT(*) FROM accounts WHERE account_number_last4 = :last4"),
            {"last4": last4}
        )
        count = check_result.scalar()
        while count > 0:
            max_id += 1
            last4 = f"{(max_id + 1000) % 10000:04d}"
            check_result = conn.execute(
                text("SELECT COUNT(*) FROM accounts WHERE account_number_last4 = :last4"),
                {"last4": last4}
            )
            count = check_result.scalar()

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
        insert_stmt = text("""
            INSERT INTO accounts (user_id, account_type, account_number_last4, nickname, balance, status, created_at, updated_at)
            VALUES (:user_id, '360 Performance Savings', :last4, NULL, :balance, 'active', :now, :now)
        """)
        
        res = conn.execute(insert_stmt, {
            "user_id": user_id,
            "last4": last4,
            "balance": request.initial_deposit,
            "now": now
        })
        
        account_id = res.lastrowid
        
        # Fetch back to confirm data
        fetch_stmt = text("""
            SELECT id, account_number_last4, nickname, balance 
            FROM accounts 
            WHERE id = :account_id
        """)
        row = conn.execute(fetch_stmt, {"account_id": account_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create account")

        return {
            "account_id": row[0],
            "account_number_last4": row[1],
            "nickname": row[2],
            "balance": row[3]
        }


@app.put("/accounts/{account_id}", operation_id="set_account_nickname")
def set_account_nickname(account_id: int, request: SetNicknameRequest):
    user_id = get_current_user_id()

    with engine.begin() as conn:
        # Verify ownership and existence
        check_stmt = text("""
            SELECT id, user_id 
            FROM accounts 
            WHERE id = :account_id
        """)
        row = conn.execute(check_stmt, {"account_id": account_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        
        db_user_id = row[1]
        if db_user_id != user_id:
            raise HTTPException(status_code=403, detail="Forbidden: You do not own this account")

        update_stmt = text("""
            UPDATE accounts 
            SET nickname = :nickname, updated_at = :now
            WHERE id = :account_id
        """)
        
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(update_stmt, {
            "nickname": request.nickname,
            "account_id": account_id,
            "now": now
        })

        return {
            "account_id": account_id,
            "nickname": request.nickname
        }


@app.post("/transfers", operation_id="schedule_recurring_transfer")
def schedule_recurring_transfer(request: ScheduleTransferRequest):
    user_id = get_current_user_id()

    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    
    if request.day_of_month < 1 or request.day_of_month > 31:
        raise HTTPException(status_code=400, detail="Day of month must be between 1 and 31")

    with engine.begin() as conn:
        # Verify source account belongs to user and is checking type? 
        # Spec says "from my existing 360 Checking account ending in 4567" but API takes IDs.
        # We should verify both accounts belong to the user.
        
        stmt_check_from = text("SELECT id, user_id, account_type FROM accounts WHERE id = :aid")
        row_from = conn.execute(stmt_check_from, {"aid": request.from_account_id}).fetchone()
        if not row_from:
            raise HTTPException(status_code=404, detail="Source account not found")
        if row_from[1] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden: Source account does not belong to user")
            
        stmt_check_to = text("SELECT id, user_id, account_type FROM accounts WHERE id = :aid")
        row_to = conn.execute(stmt_check_to, {"aid": request.to_account_id}).fetchone()
        if not row_to:
            raise HTTPException(status_code=404, detail="Destination account not found")
        if row_to[1] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden: Destination account does not belong to user")

        # Calculate next transfer date
        today = date.today()
        year = today.year
        month = today.month
        
        # Try current month first
        try:
            next_date = date(year, month, request.day_of_month)
        except ValueError:
            # Day doesn't exist in current month (e.g., Feb 30), move to next month logic handled below usually
            # But standard behavior: if invalid day for current month, go to next valid occurrence?
            # Let's assume simple increment if invalid, or clamp? 
            # Usually banks skip months where day doesn't exist or use last day.
            # For simplicity in this task context: If day > days_in_month, push to next month same day? No, that might also fail.
            # Let's stick to: if current month fails, try next month. If next month fails, keep trying until success?
            # Actually, let's just calculate strictly:
            pass
            
        # Robust calculation:
        def get_next_occurrence(day_of_month: int, start_date: date) -> date:
            y, m = start_date.year, start_date.month
            while True:
                try:
                    candidate = date(y, m, day_of_month)
                    if candidate >= start_date:
                        return candidate
                except ValueError:
                    pass
                
                m += 1
                if m > 12:
                    m = 1
                    y += 1

        next_transfer_date = get_next_occurrence(request.day_of_month, today)
        
        now_dt = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        next_date_str = next_transfer_date.isoformat()

        insert_stmt = text("""
            INSERT INTO transfers (user_id, from_account_id, to_account_id, amount, frequency, day_of_month, next_transfer_date, status, created_at, updated_at)
            VALUES (:uid, :fid, :tid, :amt, 'monthly', :dom, :ntd, 'scheduled', :now, :now)
        """)
        
        res = conn.execute(insert_stmt, {
            "uid": user_id,
            "fid": request.from_account_id,
            "tid": request.to_account_id,
            "amt": request.amount,
            "dom": request.day_of_month,
            "ntd": next_date_str,
            "now": now_dt
        })
        
        transfer_id = res.lastrowid

        return {
            "transfer_id": transfer_id,
            "from_account_id": request.from_account_id,
            "to_account_id": request.to_account_id,
            "amount": request.amount,
            "frequency": "monthly",
            "day_of_month": request.day_of_month,
            "next_transfer_date": next_date_str
        }
