import os
from datetime import datetime
from typing import List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Text, ForeignKey, func, case, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Budget(Base):
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    month = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    amount = Column(Float, nullable=False)
    transaction_date = Column(Date, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class BudgetVsActualReportItem(BaseModel):
    category_id: int
    category_name: str
    budget_amount: float
    actual_spending: float
    percentage_of_budget: float
    is_over_budget: bool

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/budget-vs-actual-report", response_model=List[BudgetVsActualReportItem], operation_id="get_budget_vs_actual_report")
def get_budget_vs_actual_report():
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Get current month in YYYY-MM format
    current_month = datetime.now().strftime("%Y-%m")
    
    # Build the query using SQLAlchemy ORM
    db = next(get_db())
    
    # Subquery to calculate total actual spending per category for the current month
    actual_spending_subq = (
        db.query(
            Transaction.category_id,
            func.sum(Transaction.amount).label("total_spending")
        )
        .filter(
            Transaction.user_id == current_user_id,
            func.strftime("%Y-%m", Transaction.transaction_date) == current_month
        )
        .group_by(Transaction.category_id)
        .subquery()
    )
    
    # Main query to join budgets, categories, and actual spending
    query = (
        db.query(
            Category.id.label("category_id"),
            Category.name.label("category_name"),
            Budget.amount.label("budget_amount"),
            case(
                (actual_spending_subq.c.total_spending.is_(None), 0.0),
                else_=actual_spending_subq.c.total_spending
            ).label("actual_spending"),
            case(
                (Budget.amount == 0, 0.0),
                (Budget.amount.is_(None), 0.0),
                else_=case(
                    (actual_spending_subq.c.total_spending.is_(None), 0.0),
                    else_=actual_spending_subq.c.total_spending / Budget.amount * 100
                )
            ).label("percentage_of_budget"),
            case(
                (Budget.amount == 0, False),
                (Budget.amount.is_(None), False),
                else_=case(
                    (actual_spending_subq.c.total_spending.is_(None), False),
                    else_=actual_spending_subq.c.total_spending > Budget.amount * 0.9
                )
            ).label("is_over_budget")
        )
        .join(Budget, Budget.category_id == Category.id)
        .outerjoin(actual_spending_subq, actual_spending_subq.c.category_id == Category.id)
        .filter(
            Budget.user_id == current_user_id,
            Budget.month == current_month
        )
        .order_by(Category.name)
    )
    
    results = query.all()
    
    # Convert to Pydantic model instances
    report_items = []
    for row in results:
        item = BudgetVsActualReportItem(
            category_id=row.category_id,
            category_name=row.category_name,
            budget_amount=row.budget_amount,
            actual_spending=row.actual_spending,
            percentage_of_budget=row.percentage_of_budget,
            is_over_budget=row.is_over_budget
        )
        report_items.append(item)
    
    return report_items
