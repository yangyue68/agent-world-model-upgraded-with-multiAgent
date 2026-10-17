import os
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="BrightPath Financial Wellness API")


class CategoryAmount(BaseModel):
    category_name: str
    amount: float


class IncomeExpenseReportResponse(BaseModel):
    report_period: str
    total_income: float
    total_expenses: float
    net_savings: float
    income_by_category: List[CategoryAmount]
    expenses_by_category: List[CategoryAmount]


def get_previous_month_str() -> str:
    """Returns the YYYY-MM string for the previous calendar month."""
    today = datetime.utcnow().date()
    # Start of current month
    first_day_current = today.replace(day=1)
    # Last day of previous month
    last_day_prev = first_day_current - timedelta(days=1)
    return last_day_prev.strftime("%Y-%m")


@app.get("/reports/income-expense", response_model=IncomeExpenseReportResponse, operation_id="get_income_expense_report")
def get_income_expense_report(
    month: Optional[str] = Query(None, description="Month and year in YYYY-MM format. Defaults to previous month if not provided.")
):
    # Determine the target period
    if month:
        try:
            # Validate format
            datetime.strptime(month, "%Y-%m")
            target_period = month
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid month format. Expected YYYY-MM.")
    else:
        target_period = get_previous_month_str()

    # Get current user ID from environment
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated.")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user ID format.")

    # Calculate date range for the target period
    # We need transactions where transaction_date falls within this month.
    # transaction_date is stored as TEXT 'YYYY-MM-DD'.
    # We can filter using LIKE 'YYYY-MM%' or extract parts. 
    # Using substr or like is safer for SQLite text dates.
    
    start_prefix = f"{target_period}%"
    
    query_sql = text("""
        SELECT 
            t.type,
            c.name as category_name,
            SUM(t.amount) as total_amount
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = :user_id
          AND t.transaction_date LIKE :period_pattern
        GROUP BY t.type, c.name
        ORDER BY t.type, c.name
    """)

    with engine.connect() as conn:
        result = conn.execute(query_sql, {
            "user_id": user_id,
            "period_pattern": start_prefix
        })
        
        rows = result.fetchall()

    total_income = 0.0
    total_expenses = 0.0
    
    income_map = {}
    expense_map = {}

    for row in rows:
        tx_type = row.type
        cat_name = row.category_name
        amount = float(row.total_amount)
        
        if tx_type == 'income':
            total_income += amount
            income_map[cat_name] = income_map.get(cat_name, 0.0) + amount
        elif tx_type == 'expense':
            total_expenses += amount
            expense_map[cat_name] = expense_map.get(cat_name, 0.0) + amount

    net_savings = total_income - total_expenses

    # Convert maps to lists of CategoryAmount objects
    income_by_category_list = [
        CategoryAmount(category_name=name, amount=amt)
        for name, amt in sorted(income_map.items())
    ]
    
    expenses_by_category_list = [
        CategoryAmount(category_name=name, amount=amt)
        for name, amt in sorted(expense_map.items())
    ]

    return IncomeExpenseReportResponse(
        report_period=target_period,
        total_income=round(total_income, 2),
        total_expenses=round(total_expenses, 2),
        net_savings=round(net_savings, 2),
        income_by_category=income_by_category_list,
        expenses_by_category=expenses_by_category_list
    )
