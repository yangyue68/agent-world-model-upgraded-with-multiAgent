import os
from datetime import date
from typing import Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

app = FastAPI()

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class BalanceAndBudgetResponse(BaseModel):
    account_balance: float
    category_remaining: float
    category_limit: float


@app.get("/primary-resource/balance-and-budget", response_model=BalanceAndBudgetResponse, operation_id="get_balance_and_budget")
def get_balance_and_budget(
    account_name: str = Query(..., description="Name of the account to check balance for"),
    category_name: str = Query(..., description="Name of the budget category to check remaining amount for")
):
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])
    
    with SessionLocal() as session:
        # Get account balance
        account_query = text("""
            SELECT current_balance 
            FROM accounts 
            WHERE user_id = :user_id AND name = :account_name
        """)
        account_result = session.execute(account_query, {
            "user_id": current_user_id,
            "account_name": account_name
        }).fetchone()
        
        if not account_result:
            raise ValueError("Account not found")
            
        account_balance = float(account_result[0])
        
        # Get category limit
        category_query = text("""
            SELECT monthly_limit 
            FROM budget_categories 
            WHERE user_id = :user_id AND name = :category_name
        """)
        category_result = session.execute(category_query, {
            "user_id": current_user_id,
            "category_name": category_name
        }).fetchone()
        
        if not category_result:
            raise ValueError("Category not found")
            
        category_limit = float(category_result[0])
        
        # Calculate remaining amount in category for current month
        # Sum all transactions for this user, account, and category in current month
        today = date.today()
        year = today.year
        month = today.month
        
        transactions_query = text("""
            SELECT SUM(amount) 
            FROM transactions 
            WHERE user_id = :user_id 
            AND budget_category_id = (
                SELECT id FROM budget_categories 
                WHERE user_id = :user_id AND name = :category_name
            )
            AND strftime('%Y-%m', transaction_date) = :year_month
        """)
        
        transactions_result = session.execute(transactions_query, {
            "user_id": current_user_id,
            "category_name": category_name,
            "year_month": f"{year}-{month:02d}"
        }).fetchone()
        
        spent_amount = float(transactions_result[0]) if transactions_result[0] else 0.0
        
        # The remaining amount is the limit minus what's been spent
        category_remaining = category_limit - abs(spent_amount)
        
        return BalanceAndBudgetResponse(
            account_balance=account_balance,
            category_remaining=category_remaining,
            category_limit=category_limit
        )
