from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text, case
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

app = FastAPI()

# Create database engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class MonthlySpendingReportResponse(BaseModel):
    category: str
    total_spent: float
    monthly_limit: float
    over_budget: bool

@app.get("/monthly_spending_report", response_model=MonthlySpendingReportResponse, operation_id="get_monthly_spending_report")
def get_monthly_spending_report(
    category: str = Query(..., description="The spending category to analyze (e.g., 'Dining Out')")
):
    db = SessionLocal()
    try:
        # Get current user ID from environment
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
        
        # Calculate total spent in the given category for current month
        # Using case statement to handle the over_budget logic
        query = text("""
            SELECT 
                :category as category,
                COALESCE(SUM(t.amount), 0) as total_spent,
                b.monthly_limit,
                CASE 
                    WHEN COALESCE(SUM(t.amount), 0) > b.monthly_limit THEN TRUE 
                    ELSE FALSE 
                END as over_budget
            FROM budgets b
            LEFT JOIN transactions t ON b.user_id = :user_id 
                AND b.category = :category 
                AND strftime('%Y-%m', t.transaction_date) = strftime('%Y-%m', 'now')
            WHERE b.user_id = :user_id AND b.category = :category
            GROUP BY b.monthly_limit
        """)
        
        result = db.execute(query, {
            "user_id": current_user_id,
            "category": category
        }).fetchone()
        
        if result is None:
            # No budget found for this user and category
            raise Exception("Budget not found for this category")
            
        return MonthlySpendingReportResponse(
            category=result.category,
            total_spent=float(result.total_spent),
            monthly_limit=float(result.monthly_limit),
            over_budget=bool(result.over_budget)
        )
    finally:
        db.close()
