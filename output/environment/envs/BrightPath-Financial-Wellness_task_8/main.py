import os
from datetime import datetime, date
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SavingsGoal(Base):
    __tablename__ = "savings_goals"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    target_amount = Column(Float, nullable=False)
    deadline = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class RecurringTransaction(Base):
    __tablename__ = "recurring_transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    transaction_type = Column(String, nullable=False)
    frequency = Column(String, nullable=False)
    day_of_month = Column(Integer)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Pydantic models
class SavingsGoalCreate(BaseModel):
    name: str
    target_amount: float
    deadline: str

class RecurringTransactionCreate(BaseModel):
    description: str
    amount: float
    transaction_type: str
    frequency: str
    day_of_month: Optional[int] = None
    start_date: str
    end_date: Optional[str] = None

# FastAPI app
app = FastAPI()

# Helper function to get current user ID
def get_current_user_id():
    return int(os.environ.get("AWM_CURRENT_USER_ID", 0))

# Create savings goal
@app.post("/savings_goals", operation_id="create_savings_goal")
def create_savings_goal(goal: SavingsGoalCreate):
    db = SessionLocal()
    try:
        user_id = get_current_user_id()
        
        # Parse deadline string to date object
        deadline_date = date.fromisoformat(goal.deadline) if goal.deadline else None
        
        db_goal = SavingsGoal(
            user_id=user_id,
            name=goal.name,
            target_amount=goal.target_amount,
            deadline=deadline_date
        )
        
        db.add(db_goal)
        db.commit()
        db.refresh(db_goal)
        
        return {
            "id": db_goal.id,
            "user_id": db_goal.user_id,
            "name": db_goal.name,
            "target_amount": db_goal.target_amount,
            "deadline": db_goal.deadline.isoformat() if db_goal.deadline else None,
            "created_at": db_goal.created_at.isoformat(),
            "updated_at": db_goal.updated_at.isoformat()
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()

# Create recurring transaction
@app.post("/recurring_transactions", operation_id="create_recurring_transaction")
def create_recurring_transaction(transaction: RecurringTransactionCreate):
    db = SessionLocal()
    try:
        user_id = get_current_user_id()
        
        # Parse start_date string to date object
        start_date = date.fromisoformat(transaction.start_date)
        
        # Parse end_date string to date object if provided
        end_date = date.fromisoformat(transaction.end_date) if transaction.end_date else None
        
        db_transaction = RecurringTransaction(
            user_id=user_id,
            description=transaction.description,
            amount=transaction.amount,
            transaction_type=transaction.transaction_type,
            frequency=transaction.frequency,
            day_of_month=transaction.day_of_month,
            start_date=start_date,
            end_date=end_date
        )
        
        db.add(db_transaction)
        db.commit()
        db.refresh(db_transaction)
        
        return {
            "id": db_transaction.id,
            "user_id": db_transaction.user_id,
            "description": db_transaction.description,
            "amount": db_transaction.amount,
            "transaction_type": db_transaction.transaction_type,
            "frequency": db_transaction.frequency,
            "day_of_month": db_transaction.day_of_month,
            "start_date": db_transaction.start_date.isoformat() if db_transaction.start_date else None,
            "end_date": db_transaction.end_date.isoformat() if db_transaction.end_date else None,
            "is_active": db_transaction.is_active,
            "created_at": db_transaction.created_at.isoformat(),
            "updated_at": db_transaction.updated_at.isoformat()
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()
