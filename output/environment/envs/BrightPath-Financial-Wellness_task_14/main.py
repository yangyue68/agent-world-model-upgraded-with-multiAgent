from fastapi import FastAPI, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, func, case, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import date
from fastapi.exceptions import HTTPException

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String)
    full_name = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class BudgetEnvelope(Base):
    __tablename__ = "budget_envelopes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
    amount = Column(Float, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
    amount = Column(Float, nullable=False)
    transaction_date = Column(Date, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

# Response Model
class CategorySpendingReport(BaseModel):
    category_name: str
    total_spent: float
    budget_allocated: float
    period_start: date
    period_end: date

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/reports/spending-by-category", response_model=CategorySpendingReport, operation_id="get_category_spending_report")
def get_category_spending_report(
    category_name: str = Query(..., description="Name of the category to generate report for"),
    db = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Get the category ID for the given category name
    category = db.query(Category).filter(
        Category.user_id == current_user_id,
        Category.name == category_name
    ).first()
    
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Get the budget envelope for this category for the last quarter
    budget_envelope = db.query(BudgetEnvelope).filter(
        BudgetEnvelope.user_id == current_user_id,
        BudgetEnvelope.category_id == category.id,
        BudgetEnvelope.period_start <= "2024-06-30",
        BudgetEnvelope.period_end >= "2024-04-01"
    ).first()
    
    if not budget_envelope:
        raise HTTPException(status_code=404, detail="Budget envelope not found")
    
    # Calculate total spent for this category in the last quarter
    total_spent = db.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id == current_user_id,
        Transaction.category_id == category.id,
        Transaction.transaction_date >= "2024-04-01",
        Transaction.transaction_date <= "2024-06-30"
    ).scalar() or 0.0
    
    return CategorySpendingReport(
        category_name=category.name,
        total_spent=float(total_spent),
        budget_allocated=float(budget_envelope.amount),
        period_start=budget_envelope.period_start,
        period_end=budget_envelope.period_end
    )
