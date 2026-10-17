import os
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, func, case, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Budget(Base):
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
    month = Column(String)
    budget_amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
    amount = Column(Float, nullable=False)
    transaction_date = Column(Date, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

app = FastAPI()

# Pydantic models
class MonthlyOverspendingReportResponse(BaseModel):
    highest_overspend_month: str
    category: str
    overage_amount: float

class MonthlySpendingByCategoryItem(BaseModel):
    month: str
    category_id: int
    category_name: str
    total_spent: float

class MonthlyBudgetsItem(BaseModel):
    month: str
    category_id: int
    category_name: str
    budget_amount: float

# Helper functions
def get_current_user_id():
    return int(os.environ.get("AWM_CURRENT_USER_ID", 0))

def get_months_back(months_back: int = 6):
    """Calculate start date based on months_back"""
    today = datetime.now().date()
    # Calculate the first day of the month 'months_back' months ago
    start_date = today.replace(day=1) - timedelta(days=30*months_back)
    # Adjust to ensure we're at the beginning of the month
    start_date = start_date.replace(day=1)
    return start_date

@app.get("/monthly_overspending_report", response_model=MonthlyOverspendingReportResponse, operation_id="get_monthly_overspending_report")
def get_monthly_overspending_report(months_back: Optional[int] = Query(6, ge=1)):
    db = SessionLocal()
    try:
        # Get the current user ID
        current_user_id = get_current_user_id()
        
        # Calculate the date range
        start_date = get_months_back(months_back)
        
        # Create a subquery to get all months in the range
        # We'll use a CTE approach to calculate spending vs budget for each month/category
        
        # First, let's get spending by month and category
        spending_subq = db.query(
            Transaction.transaction_date.label('date'),
            Transaction.category_id,
            func.sum(Transaction.amount).label('total_spent')
        ).filter(
            Transaction.user_id == current_user_id,
            Transaction.transaction_date >= start_date
        ).group_by(
            Transaction.transaction_date,
            Transaction.category_id
        ).subquery()
        
        # Then join with budgets to compare
        # We need to extract the month from the date
        month_extract = func.strftime('%Y-%m', spending_subq.c.date)
        
        # Join with budgets to get budget amounts
        joined_query = db.query(
            month_extract.label('month'),
            spending_subq.c.category_id,
            Category.name.label('category_name'),
            spending_subq.c.total_spent,
            Budget.budget_amount
        ).join(
            Category, Category.id == spending_subq.c.category_id
        ).join(
            Budget, 
            (Budget.user_id == current_user_id) &
            (Budget.category_id == spending_subq.c.category_id) &
            (Budget.month == month_extract)
        ).filter(
            spending_subq.c.total_spent > Budget.budget_amount
        )
        
        # Find the maximum overage
        overage_query = db.query(
            month_extract.label('month'),
            Category.name.label('category_name'),
            (spending_subq.c.total_spent - Budget.budget_amount).label('overage')
        ).join(
            Category, Category.id == spending_subq.c.category_id
        ).join(
            Budget, 
            (Budget.user_id == current_user_id) &
            (Budget.category_id == spending_subq.c.category_id) &
            (Budget.month == month_extract)
        ).filter(
            spending_subq.c.total_spent > Budget.budget_amount
        ).order_by(
            (spending_subq.c.total_spent - Budget.budget_amount).desc()
        ).limit(1)
        
        result = overage_query.first()
        
        if not result:
            # If no overspending found, return empty response
            return MonthlyOverspendingReportResponse(
                highest_overspend_month="",
                category="",
                overage_amount=0.0
            )
            
        return MonthlyOverspendingReportResponse(
            highest_overspend_month=result.month,
            category=result.category_name,
            overage_amount=float(result.overage)
        )
        
    finally:
        db.close()

@app.get("/monthly_spending_by_category", response_model=List[MonthlySpendingByCategoryItem], operation_id="get_monthly_spending_by_category")
def get_monthly_spending_by_category(
    user_id: int = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...)
):
    db = SessionLocal()
    try:
        # Parse dates
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        
        # Get spending by month and category
        query = db.query(
            func.strftime('%Y-%m', Transaction.transaction_date).label('month'),
            Transaction.category_id,
            Category.name.label('category_name'),
            func.sum(Transaction.amount).label('total_spent')
        ).join(
            Category, Category.id == Transaction.category_id
        ).filter(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= start_dt,
            Transaction.transaction_date <= end_dt
        ).group_by(
            func.strftime('%Y-%m', Transaction.transaction_date),
            Transaction.category_id,
            Category.name
        ).order_by(
            func.strftime('%Y-%m', Transaction.transaction_date),
            Transaction.category_id
        )
        
        results = query.all()
        
        return [
            MonthlySpendingByCategoryItem(
                month=row.month,
                category_id=row.category_id,
                category_name=row.category_name,
                total_spent=float(row.total_spent)
            ) for row in results
        ]
        
    finally:
        db.close()

@app.get("/monthly_budgets", response_model=List[MonthlyBudgetsItem], operation_id="get_monthly_budgets")
def get_monthly_budgets(
    user_id: int = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...)
):
    db = SessionLocal()
    try:
        # Parse dates
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        
        # Get budgets by month and category
        query = db.query(
            Budget.month,
            Budget.category_id,
            Category.name.label('category_name'),
            Budget.budget_amount
        ).join(
            Category, Category.id == Budget.category_id
        ).filter(
            Budget.user_id == user_id,
            func.strftime('%Y-%m', func.date(Budget.created_at)) >= func.strftime('%Y-%m', start_dt),
            func.strftime('%Y-%m', func.date(Budget.created_at)) <= func.strftime('%Y-%m', end_dt)
        ).order_by(
            Budget.month,
            Budget.category_id
        )
        
        results = query.all()
        
        return [
            MonthlyBudgetsItem(
                month=row.month,
                category_id=row.category_id,
                category_name=row.category_name,
                budget_amount=float(row.budget_amount)
            ) for row in results
        ]
        
    finally:
        db.close()
