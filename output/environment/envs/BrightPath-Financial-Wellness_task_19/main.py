import os
from datetime import date, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="BrightPath Financial Wellness API")


class RecurringTransactionResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    amount: float
    transaction_type: str
    frequency: str
    next_occurrence_date: str
    account_name: str
    category_name: Optional[str] = None


class TotalExpensesResponse(BaseModel):
    total_expenses: float


def get_current_user_id() -> int:
    """Reads the current user ID from the environment."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


def get_upcoming_month_range() -> tuple[date, date]:
    """
    Calculates the start and end dates for the upcoming calendar month.
    Upcoming month means the month following the current one.
    Example: If today is 2024-05-15, upcoming month is June 2024 (2024-06-01 to 2024-06-30).
    """
    today = date.today()
    
    # Find the first day of the current month
    first_day_of_current_month = today.replace(day=1)
    
    # Calculate the first day of the next month
    if today.month == 12:
        first_day_of_next_month = date(today.year + 1, 1, 1)
    else:
        first_day_of_next_month = date(today.year, today.month + 1, 1)
        
    # Calculate the last day of the next month
    if first_day_of_next_month.month == 12:
        last_day_of_next_month = date(first_day_of_next_month.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day_of_next_month = date(first_day_of_next_month.year, first_day_of_next_month.month + 1, 1) - timedelta(days=1)
        
    return first_day_of_next_month, last_day_of_next_month


@app.get("/recurring-transactions/upcoming-month", response_model=List[RecurringTransactionResponse], operation_id="get_upcoming_month_recurring_transactions")
def get_upcoming_month_recurring_transactions():
    """
    Retrieve all active recurring transactions scheduled for the upcoming month.
    """
    user_id = get_current_user_id()
    start_date, end_date = get_upcoming_month_range()
    
    db = SessionLocal()
    try:
        query = text("""
            SELECT 
                rt.id,
                rt.name,
                rt.description,
                rt.amount,
                rt.transaction_type,
                rt.frequency,
                rt.next_occurrence_date,
                a.name as account_name,
                c.name as category_name
            FROM recurring_transactions rt
            JOIN accounts a ON rt.account_id = a.id
            LEFT JOIN categories c ON rt.category_id = c.id
            WHERE rt.user_id = :user_id
              AND rt.is_active = 1
              AND rt.next_occurrence_date >= :start_date
              AND rt.next_occurrence_date <= :end_date
            ORDER BY rt.next_occurrence_date ASC
        """)
        
        results = db.execute(query, {
            "user_id": user_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        }).fetchall()
        
        transactions = []
        for row in results:
            transactions.append(RecurringTransactionResponse(
                id=row.id,
                name=row.name,
                description=row.description,
                amount=float(row.amount),
                transaction_type=row.transaction_type,
                frequency=row.frequency,
                next_occurrence_date=str(row.next_occurrence_date),
                account_name=row.account_name,
                category_name=row.category_name
            ))
            
        return transactions
    finally:
        db.close()


@app.get("/recurring-transactions/upcoming-month/total-expenses", response_model=TotalExpensesResponse, operation_id="calculate_total_upcoming_expenses")
def calculate_total_upcoming_expenses():
    """
    Calculate the total expected expenses from recurring transactions in the upcoming month.
    """
    user_id = get_current_user_id()
    start_date, end_date = get_upcoming_month_range()
    
    db = SessionLocal()
    try:
        query = text("""
            SELECT COALESCE(SUM(rt.amount), 0.0) as total
            FROM recurring_transactions rt
            WHERE rt.user_id = :user_id
              AND rt.is_active = 1
              AND rt.transaction_type = 'expense'
              AND rt.next_occurrence_date >= :start_date
              AND rt.next_occurrence_date <= :end_date
        """)
        
        result = db.execute(query, {
            "user_id": user_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        }).scalar_one_or_none()
        
        total_expenses = float(result) if result is not None else 0.0
        
        return TotalExpensesResponse(total_expenses=total_expenses)
    finally:
        db.close()
