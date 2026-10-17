from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text, case
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

app = FastAPI()

# Create SQLAlchemy engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class CategoryReport(BaseModel):
    category_name: str
    total_amount: float

class MonthlyExpenseReportResponse(BaseModel):
    report_period: str
    total_expenses: float
    top_categories: list[CategoryReport]

@app.get("/monthly-expense-report", response_model=MonthlyExpenseReportResponse, operation_id="generate_monthly_expense_report")
def generate_monthly_expense_report(reporting_month: str = Query(..., description="The month and year of the report in YYYY-MM format")):
    # Get the current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Validate reporting_month format (YYYY-MM)
    try:
        report_date = datetime.strptime(reporting_month, "%Y-%m")
    except ValueError:
        raise ValueError("Invalid reporting_month format. Expected YYYY-MM")
    
    # Prepare the query
    query = text("""
        SELECT 
            c.name AS category_name,
            SUM(e.amount) AS total_amount
        FROM expenses e
        JOIN categories c ON e.category_id = c.id
        WHERE e.user_id = :user_id 
            AND strftime('%Y-%m', e.expense_date) = :reporting_month
        GROUP BY c.id, c.name
        ORDER BY total_amount DESC
        LIMIT 3
    """)
    
    # Execute query
    with engine.connect() as conn:
        result = conn.execute(query, {"user_id": current_user_id, "reporting_month": reporting_month})
        
        # Calculate total expenses for the period
        total_query = text("""
            SELECT SUM(amount) AS total_expenses
            FROM expenses
            WHERE user_id = :user_id 
                AND strftime('%Y-%m', expense_date) = :reporting_month
        """)
        
        total_result = conn.execute(total_query, {"user_id": current_user_id, "reporting_month": reporting_month})
        total_expenses = total_result.fetchone()[0] or 0.0
        
        # Process results
        top_categories = []
        for row in result:
            top_categories.append(CategoryReport(
                category_name=row.category_name,
                total_amount=float(row.total_amount)
            ))
    
    return MonthlyExpenseReportResponse(
        report_period=reporting_month,
        total_expenses=float(total_expenses),
        top_categories=top_categories
    )
