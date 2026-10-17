import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException
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


class TransactionItem(BaseModel):
    transaction_date: str
    description: str
    miles_amount: int


class WelcomeBonusProgress(BaseModel):
    bonus_miles_amount: int
    required_spend: float
    current_spend: float
    progress_percentage: float
    is_completed: bool


class RewardsSummaryResponse(BaseModel):
    rewards_balance: int
    last_10_transactions: List[TransactionItem]
    welcome_bonus_progress: WelcomeBonusProgress


@app.get("/primary-resource", response_model=RewardsSummaryResponse, operation_id="get_user_rewards_summary")
def get_user_rewards_summary():
    # Get current user ID from environment
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    db = SessionLocal()
    try:
        # Find the card for this user (assuming one Venture X card per user based on scenario)
        # We look for a card associated with the user. The spec implies "my Capital One Venture X".
        # Let's find any card belonging to the user first. If multiple, we might need logic, 
        # but typically in these scenarios, there's a primary or single relevant account.
        # Based on schema, cards table has user_id.
        
        card_query = text("""
            SELECT c.id as card_id
            FROM cards c
            WHERE c.user_id = :user_id
            LIMIT 1
        """)
        card_result = db.execute(card_query, {"user_id": user_id}).fetchone()
        
        if not card_result:
            raise HTTPException(status_code=404, detail="No card found for user")
        
        card_id = card_result.card_id
        
        # Get rewards account for this card
        rewards_account_query = text("""
            SELECT id as rewards_account_id, miles_balance
            FROM rewards_accounts
            WHERE card_id = :card_id
        """)
        rewards_account_result = db.execute(rewards_account_query, {"card_id": card_id}).fetchone()
        
        if not rewards_account_result:
            raise HTTPException(status_code=404, detail="No rewards account found for card")
            
        rewards_account_id = rewards_account_result.rewards_account_id
        rewards_balance = rewards_account_result.miles_balance
        
        # Get last 10 transactions
        transactions_query = text("""
            SELECT transaction_date, description, miles_amount
            FROM miles_transactions
            WHERE rewards_account_id = :rewards_account_id
            ORDER BY transaction_date DESC
            LIMIT 10
        """)
        tx_results = db.execute(transactions_query, {"rewards_account_id": rewards_account_id}).fetchall()
        
        last_10_transactions = []
        for row in tx_results:
            # Format date string appropriately if needed, though DB returns datetime object usually converted to str by SQLAlchemy/SQLite driver depending on config.
            # Assuming standard ISO-like string or datetime object that Pydantic can handle.
            # SQLite DATETIME often comes back as string 'YYYY-MM-DD HH:MM:SS'.
            # The API spec expects format: date-time.
            dt_val = row.transaction_date
            if hasattr(dt_val, 'isoformat'):
                dt_str = dt_val.isoformat()
            else:
                # Replace space with T for strict ISO 8601 compliance if it's just a string
                dt_str = str(dt_val).replace(' ', 'T')
                
            last_10_transactions.append(TransactionItem(
                transaction_date=dt_str,
                description=row.description,
                miles_amount=row.miles_amount
            ))
            
        # Get welcome bonus progress
        bonus_query = text("""
            SELECT bonus_miles_amount, required_spend, current_spend, is_completed
            FROM welcome_bonus_progress
            WHERE rewards_account_id = :rewards_account_id
        """)
        bonus_result = db.execute(bonus_query, {"rewards_account_id": rewards_account_id}).fetchone()
        
        if not bonus_result:
            # Default values if no bonus tracked? Spec requires this object. 
            # Given initial snapshot, all accounts have entries. But safe fallback:
            welcome_bonus_progress = WelcomeBonusProgress(
                bonus_miles_amount=0,
                required_spend=0.0,
                current_spend=0.0,
                progress_percentage=0.0,
                is_completed=True # Or False? Usually incomplete if 0 spend. Let's assume True if no data means nothing pending? 
                                 # Actually, better to error out or provide zeros. 
                                 # Since schema says UNIQUE and FK, likely exists.
            )
        else:
            b_amt = bonus_result.bonus_miles_amount
            req_sp = bonus_result.required_spend
            cur_sp = bonus_result.current_spend
            comp_flag = bonus_result.is_completed
            
            # Calculate percentage
            if req_sp > 0:
                pct = (cur_sp / req_sp) * 100.0
            else:
                pct = 0.0
                
            # Cap at 100%? Not specified, but logical. However, raw calculation is safer unless specified.
            # Let's stick to raw calculation.
            
            is_completed_bool = bool(comp_flag)
            
            welcome_bonus_progress = WelcomeBonusProgress(
                bonus_miles_amount=b_amt,
                required_spend=float(req_sp),
                current_spend=float(cur_sp),
                progress_percentage=float(pct),
                is_completed=is_completed_bool
            )

        return RewardsSummaryResponse(
            rewards_balance=rewards_balance,
            last_10_transactions=last_10_transactions,
            welcome_bonus_progress=welcome_bonus_progress
        )
        
    finally:
        db.close()
