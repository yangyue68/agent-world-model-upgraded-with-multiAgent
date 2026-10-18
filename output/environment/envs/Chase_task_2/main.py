import os
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI()

# --- Pydantic Models ---

class AccountSummaryItem(BaseModel):
    id: int
    display_name: str
    account_number_last4: str
    current_balance: float
    currency: str

class PrimaryResourceSummaryResponse(BaseModel):
    accounts: List[AccountSummaryItem]

class SearchAccountItem(BaseModel):
    id: int
    user_id: int
    account_type_id: int
    account_number_last4: str
    display_name: str
    current_balance: float
    currency: str
    is_active: int

class BulkSearchRequest(BaseModel):
    account_number_last4s: List[str]

# --- Endpoints ---

@app.get("/primary-resource-summary", response_model=PrimaryResourceSummaryResponse, operation_id="get_primary_resource_summary")
def get_primary_resource_summary():
    """
    Retrieve a summary of specified linked account balances.
    Fetches the current balances of specific Chase accounts including the Sapphire Reserve credit card ending in 8888, 
    the Premier Savings account ending in 9012, and an auto loan account ending in 7777 for the authenticated user.
    """
    db = SessionLocal()
    try:
        # Get current user ID from environment
        user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="User ID not found in environment")
        
        user_id = int(user_id_str)

        # Define the target last-4 digits as per the task description
        target_last4s = ["8888", "9012", "7777"]

        # Query the database for these specific accounts belonging to the current user
        # Using explicit placeholders for each value to avoid SQL syntax errors with IN clause tuples
        placeholders = ", ".join([f":last4_{i}" for i in range(len(target_last4s))])
        query = text(f"""
            SELECT 
                id, 
                display_name, 
                account_number_last4, 
                current_balance, 
                currency
            FROM accounts
            WHERE user_id = :user_id
            AND account_number_last4 IN ({placeholders})
            ORDER BY account_number_last4
        """)

        params = {"user_id": user_id}
        for i, last4 in enumerate(target_last4s):
            params[f"last4_{i}"] = last4

        result = db.execute(query, params)
        rows = result.fetchall()

        accounts = []
        for row in rows:
            accounts.append(AccountSummaryItem(
                id=row[0],
                display_name=row[1],
                account_number_last4=row[2],
                current_balance=row[3],
                currency=row[4]
            ))

        return PrimaryResourceSummaryResponse(accounts=accounts)
    finally:
        db.close()


@app.get("/accounts/search", response_model=List[SearchAccountItem], operation_id="search_accounts_by_last_four")
def search_accounts_by_last_four(account_number_last4: str = Query(..., description="Last four digits of the account number to search for")):
    """
    Search for accounts by the last four digits of the account number.
    Returns a list of accounts associated with the authenticated user that match the provided last four digits of the account number.
    """
    db = SessionLocal()
    try:
        user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="User ID not found in environment")
        
        user_id = int(user_id_str)

        query = text("""
            SELECT 
                id, 
                user_id, 
                account_type_id, 
                account_number_last4, 
                display_name, 
                current_balance, 
                currency, 
                is_active
            FROM accounts
            WHERE user_id = :user_id
            AND account_number_last4 = :last4
        """)

        result = db.execute(query, {"user_id": user_id, "last4": account_number_last4})
        rows = result.fetchall()

        accounts = []
        for row in rows:
            accounts.append(SearchAccountItem(
                id=row[0],
                user_id=row[1],
                account_type_id=row[2],
                account_number_last4=row[3],
                display_name=row[4],
                current_balance=row[5],
                currency=row[6],
                is_active=row[7]
            ))

        return accounts
    finally:
        db.close()


@app.post("/accounts/bulk-search", response_model=List[SearchAccountItem], operation_id="bulk_search_accounts_by_last_fours")
def bulk_search_accounts_by_last_fours(request: BulkSearchRequest):
    """
    Bulk search for accounts by last four digits.
    Returns a list of accounts associated with the authenticated user that match any of the provided last four digits of the account numbers.
    """
    db = SessionLocal()
    try:
        user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="User ID not found in environment")
        
        user_id = int(user_id_str)
        last4s = request.account_number_last4s

        if not last4s:
            return []

        # Create placeholders for the IN clause
        placeholders = ", ".join([f":last4_{i}" for i in range(len(last4s))])
        
        query = text(f"""
            SELECT 
                id, 
                user_id, 
                account_type_id, 
                account_number_last4, 
                display_name, 
                current_balance, 
                currency, 
                is_active
            FROM accounts
            WHERE user_id = :user_id
            AND account_number_last4 IN ({placeholders})
        """)

        params = {"user_id": user_id}
        for i, last4 in enumerate(last4s):
            params[f"last4_{i}"] = last4

        result = db.execute(query, params)
        rows = result.fetchall()

        accounts = []
        for row in rows:
            accounts.append(SearchAccountItem(
                id=row[0],
                user_id=row[1],
                account_type_id=row[2],
                account_number_last4=row[3],
                display_name=row[4],
                current_balance=row[5],
                currency=row[6],
                is_active=row[7]
            ))

        return accounts
    finally:
        db.close()
