import os
from datetime import date
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Float, Text, ForeignKey, CheckConstraint, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

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
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"))
    amount = Column(Float, nullable=False)
    currency = Column(String, default="USD")
    description = Column(Text)
    expense_date = Column(Date, nullable=False)
    payment_method = Column(String, default="cash")
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    
    # Relationships
    category = relationship("Category")

class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())

class ExpenseTag(Base):
    __tablename__ = "expense_tags"
    expense_id = Column(Integer, ForeignKey("expenses.id"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tags.id"), primary_key=True)
    created_at = Column(DateTime, default=func.current_timestamp())

# Request and Response Models
class LogExpenseRequest(BaseModel):
    amount: float
    expense_date: date
    category_name: str
    tag_names: List[str]
    description: Optional[str] = None

class ExpenseResponse(BaseModel):
    id: int
    user_id: int
    category_id: int
    amount: float
    currency: str
    description: Optional[str]
    expense_date: date
    payment_method: str
    created_at: str
    updated_at: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/expenses", response_model=ExpenseResponse, operation_id="log_cash_expense")
def log_cash_expense(request: LogExpenseRequest):
    db = next(get_db())
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    
    # Validate category exists for current user
    category = db.query(Category).filter(
        Category.user_id == current_user_id,
        Category.name == request.category_name
    ).first()
    
    if not category:
        raise HTTPException(status_code=400, detail="Category not found for current user")
    
    # Validate all tags exist for current user
    existing_tags = db.query(Tag).filter(
        Tag.user_id == current_user_id,
        Tag.name.in_(request.tag_names)
    ).all()
    
    existing_tag_names = {tag.name for tag in existing_tags}
    missing_tags = set(request.tag_names) - existing_tag_names
    
    if missing_tags:
        raise HTTPException(status_code=400, detail=f"Tags not found for current user: {missing_tags}")
    
    # Create expense
    expense = Expense(
        user_id=current_user_id,
        category_id=category.id,
        amount=request.amount,
        currency="USD",
        description=request.description,
        expense_date=request.expense_date,
        payment_method="cash"
    )
    
    db.add(expense)
    db.flush()  # Get the ID without committing
    
    # Link tags to expense
    for tag in existing_tags:
        expense_tag = ExpenseTag(
            expense_id=expense.id,
            tag_id=tag.id
        )
        db.add(expense_tag)
    
    db.commit()
    db.refresh(expense)
    
    # Convert datetime to string for response
    return ExpenseResponse(
        id=expense.id,
        user_id=expense.user_id,
        category_id=expense.category_id,
        amount=expense.amount,
        currency=expense.currency,
        description=expense.description,
        expense_date=expense.expense_date,
        payment_method=expense.payment_method,
        created_at=expense.created_at.isoformat(),
        updated_at=expense.updated_at.isoformat()
    )
