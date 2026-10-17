import os
from datetime import date, datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Float, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class BudgetPeriod(Base):
    __tablename__ = 'budget_periods'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    name = Column(String, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("start_date < end_date"),)

class BudgetCategory(Base):
    __tablename__ = 'budget_categories'
    id = Column(Integer, primary_key=True)
    budget_period_id = Column(Integer, ForeignKey('budget_periods.id'), nullable=False)
    name = Column(String, nullable=False)
    limit_amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("limit_amount >= 0"),)

class Alert(Base):
    __tablename__ = 'alerts'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    budget_period_id = Column(Integer, ForeignKey('budget_periods.id'))
    category_id = Column(Integer, ForeignKey('budget_categories.id'))
    threshold_percent = Column(Float, nullable=False)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("threshold_percent > 0 AND threshold_percent <= 100"),
        CheckConstraint("is_active IN (0,1)"),
    )

# Pydantic Models
class CreateBudgetPeriodRequest(BaseModel):
    name: str
    start_date: str
    end_date: str

class CreateBudgetPeriodResponse(BaseModel):
    id: int
    name: str
    start_date: str
    end_date: str

class CreateBudgetCategoryRequest(BaseModel):
    budget_period_id: int
    name: str
    limit_amount: float

class CreateBudgetCategoryResponse(BaseModel):
    id: int
    budget_period_id: int
    name: str
    limit_amount: float

class CreateSpendingAlertRequest(BaseModel):
    budget_period_id: Optional[int] = None
    category_id: Optional[int] = None
    threshold_percent: float

class CreateSpendingAlertResponse(BaseModel):
    id: int
    user_id: int
    budget_period_id: Optional[int]
    category_id: Optional[int]
    threshold_percent: float
    is_active: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/budget_periods", response_model=CreateBudgetPeriodResponse, operation_id="create_budget_period")
def create_budget_period(request: CreateBudgetPeriodRequest, db: Session = Depends(get_db)):
    user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Validate dates
    start_date = date.fromisoformat(request.start_date)
    end_date = date.fromisoformat(request.end_date)
    
    if start_date >= end_date:
        raise HTTPException(status_code=400, detail="Start date must be before end date")
    
    # Create new budget period
    budget_period = BudgetPeriod(
        user_id=user_id,
        name=request.name,
        start_date=start_date,
        end_date=end_date
    )
    
    db.add(budget_period)
    db.commit()
    db.refresh(budget_period)
    
    return CreateBudgetPeriodResponse(
        id=budget_period.id,
        name=budget_period.name,
        start_date=str(budget_period.start_date),
        end_date=str(budget_period.end_date)
    )

@app.post("/budget_categories", response_model=CreateBudgetCategoryResponse, operation_id="create_budget_category")
def create_budget_category(request: CreateBudgetCategoryRequest, db: Session = Depends(get_db)):
    user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify budget period exists and belongs to current user
    budget_period = db.query(BudgetPeriod).filter(
        BudgetPeriod.id == request.budget_period_id,
        BudgetPeriod.user_id == user_id
    ).first()
    
    if not budget_period:
        raise HTTPException(status_code=404, detail="Budget period not found")
    
    # Create new budget category
    budget_category = BudgetCategory(
        budget_period_id=request.budget_period_id,
        name=request.name,
        limit_amount=request.limit_amount
    )
    
    db.add(budget_category)
    db.commit()
    db.refresh(budget_category)
    
    return CreateBudgetCategoryResponse(
        id=budget_category.id,
        budget_period_id=budget_category.budget_period_id,
        name=budget_category.name,
        limit_amount=budget_category.limit_amount
    )

@app.post("/alerts", response_model=CreateSpendingAlertResponse, operation_id="create_spending_alert")
def create_spending_alert(request: CreateSpendingAlertRequest, db: Session = Depends(get_db)):
    user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Validate that either budget_period_id or category_id is provided
    if not request.budget_period_id and not request.category_id:
        raise HTTPException(status_code=400, detail="Either budget_period_id or category_id must be provided")
    
    # If category_id is provided, verify it exists and belongs to current user
    if request.category_id:
        category = db.query(BudgetCategory).filter(
            BudgetCategory.id == request.category_id
        ).first()
        
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")
            
        # Verify that the category belongs to a budget period that belongs to current user
        budget_period = db.query(BudgetPeriod).filter(
            BudgetPeriod.id == category.budget_period_id,
            BudgetPeriod.user_id == user_id
        ).first()
        
        if not budget_period:
            raise HTTPException(status_code=404, detail="Category does not belong to current user's budget period")
    
    # If budget_period_id is provided, verify it belongs to current user
    if request.budget_period_id:
        budget_period = db.query(BudgetPeriod).filter(
            BudgetPeriod.id == request.budget_period_id,
            BudgetPeriod.user_id == user_id
        ).first()
        
        if not budget_period:
            raise HTTPException(status_code=404, detail="Budget period not found")
    
    # Create new alert
    alert = Alert(
        user_id=user_id,
        budget_period_id=request.budget_period_id,
        category_id=request.category_id,
        threshold_percent=request.threshold_percent
    )
    
    db.add(alert)
    db.commit()
    db.refresh(alert)
    
    return CreateSpendingAlertResponse(
        id=alert.id,
        user_id=alert.user_id,
        budget_period_id=alert.budget_period_id,
        category_id=alert.category_id,
        threshold_percent=alert.threshold_percent,
        is_active=alert.is_active
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
