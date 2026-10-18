import os
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, Float, Date, DateTime, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI(title="Charles Schwab API", description="Retrieve portfolio metrics for Charles Schwab accounts.")

# --- Pydantic Models ---

class AccountResponse(BaseModel):
    id: int
    account_number: str

class PortfolioMetricsResponse(BaseModel):
    account_id: int
    metric_date: str
    volatility_30d_annualized: float
    sharpe_ratio_30d: float

# --- Endpoints ---

@app.get("/primary-resource/accounts", response_model=List[AccountResponse], operation_id="find_accounts", tags=["PrimaryResource"])
def find_accounts(account_type: str = Query(..., description="Account type to filter by")):
    """
    Filter accounts by the specified account type and return matching account IDs and numbers for the authenticated user.
    """
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    db = SessionLocal()
    try:
        # Using raw SQL to ensure we respect the schema exactly and handle potential edge cases cleanly
        query = text("""
            SELECT id, account_number 
            FROM accounts 
            WHERE user_id = :user_id AND account_type = :account_type
        """)
        
        results = db.execute(query, {"user_id": current_user_id, "account_type": account_type}).fetchall()
        
        accounts = []
        for row in results:
            accounts.append(AccountResponse(id=row[0], account_number=row[1]))
            
        return accounts
    finally:
        db.close()

@app.get("/primary-resource/portfolio-metrics", response_model=PortfolioMetricsResponse, operation_id="get_portfolio_metrics", tags=["PrimaryResource"])
def get_portfolio_metrics(account_id: int = Query(..., description="The account ID of the 'Schwab Intelligent Portfolios Premium' account")):
    """
    Fetches the latest 30-day annualized volatility and Sharpe ratio for the 'Schwab Intelligent Portfolios Premium' account associated with the authenticated user.
    """
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    db = SessionLocal()
    try:
        # Verify ownership and account type first
        verify_query = text("""
            SELECT id FROM accounts 
            WHERE id = :account_id AND user_id = :user_id AND account_type = 'Schwab Intelligent Portfolios Premium'
        """)
        
        owner_check = db.execute(verify_query, {"account_id": account_id, "user_id": current_user_id}).fetchone()
        
        if not owner_check:
            raise HTTPException(status_code=404, detail="Account not found or access denied")

        # Get the latest metrics for this account
        metrics_query = text("""
            SELECT account_id, metric_date, volatility_30d_annualized, sharpe_ratio_30d
            FROM portfolio_metrics
            WHERE account_id = :account_id
            ORDER BY metric_date DESC
            LIMIT 1
        """)
        
        result = db.execute(metrics_query, {"account_id": account_id}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="No metrics found for this account")
            
        return PortfolioMetricsResponse(
            account_id=result[0],
            metric_date=result[1],
            volatility_30d_annualized=result[2],
            sharpe_ratio_30d=result[3]
        )
    finally:
        db.close()
