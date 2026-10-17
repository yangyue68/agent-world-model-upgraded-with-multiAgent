from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import date, datetime

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class SavingsGoal(Base):
    __tablename__ = "savings_goals"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False)
    target_amount = Column(Float, nullable=False)
    current_amount = Column(Float, default=0.0)
    target_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class SavingsGoalContribution(Base):
    __tablename__ = "savings_goal_contributions"
    id = Column(Integer, primary_key=True)
    goal_id = Column(Integer, ForeignKey("savings_goals.id"))
    amount = Column(Float, nullable=False)
    contribution_date = Column(Date, nullable=False)
    note = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class SavingsGoalProgressResponse(BaseModel):
    id: int
    name: str
    target_amount: float
    current_amount: float
    progress_percentage: float
    target_date: date
    days_remaining: int

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/savings-goals/{goal_id}", response_model=SavingsGoalProgressResponse, operation_id="get_savings_goal_progress")
def get_savings_goal_progress(goal_id: int):
    db = next(get_db())
    
    # Get the current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Query the savings goal
    goal = db.query(SavingsGoal).filter(
        SavingsGoal.id == goal_id,
        SavingsGoal.user_id == current_user_id
    ).first()
    
    if not goal:
        raise HTTPException(status_code=404, detail="Savings goal not found")
    
    # Calculate current amount using contributions
    current_amount = db.query(func.sum(SavingsGoalContribution.amount)).filter(
        SavingsGoalContribution.goal_id == goal_id
    ).scalar() or 0.0
    
    # Update goal's current_amount if needed
    if current_amount != goal.current_amount:
        goal.current_amount = current_amount
        db.commit()
    
    # Calculate progress percentage
    progress_percentage = (current_amount / goal.target_amount) * 100 if goal.target_amount > 0 else 0.0
    
    # Calculate days remaining
    today = date.today()
    days_remaining = (goal.target_date - today).days if goal.target_date > today else 0
    
    return SavingsGoalProgressResponse(
        id=goal.id,
        name=goal.name,
        target_amount=goal.target_amount,
        current_amount=current_amount,
        progress_percentage=progress_percentage,
        target_date=goal.target_date,
        days_remaining=days_remaining
    )
