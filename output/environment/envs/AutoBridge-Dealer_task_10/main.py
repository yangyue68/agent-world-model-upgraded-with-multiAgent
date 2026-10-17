import os
from datetime import date, datetime
from typing import Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class SpiffPlan(Base):
    __tablename__ = "spiff_plans"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    award_amount = Column(Float, nullable=False)
    effective_start_date = Column(Date, nullable=False)
    effective_end_date = Column(Date, nullable=False)
    status = Column(String, nullable=False, default="active")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("award_amount >= 0"),
        CheckConstraint("status IN ('active', 'inactive', 'expired')"),
        CheckConstraint("effective_end_date >= effective_start_date"),
    )

class SpiffPlanCondition(Base):
    __tablename__ = "spiff_plan_conditions"
    id = Column(Integer, primary_key=True, index=True)
    spiff_plan_id = Column(Integer, ForeignKey("spiff_plans.id"), nullable=False)
    condition_type = Column(String, nullable=False)
    operator = Column(String, nullable=False)
    threshold_value = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("condition_type IN ('gross_profit_threshold', 'vehicle_type', 'sales_volume', 'other')"),
        CheckConstraint("operator IN ('>', '>=', '<', '<=', '=', '!=')"),
    )

class SpiffAward(Base):
    __tablename__ = "spiff_awards"
    id = Column(Integer, primary_key=True, index=True)
    spiff_plan_id = Column(Integer, ForeignKey("spiff_plans.id"), nullable=False)
    salesperson_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    vehicle_sale_id = Column(Integer, ForeignKey("vehicle_sales.id"), nullable=False)
    award_amount = Column(Float, nullable=False)
    awarded_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("award_amount >= 0"),
        CheckConstraint("spiff_plan_id IS NOT NULL AND vehicle_sale_id IS NOT NULL", name="unique_spiff_plan_vehicle"),
    )

class VehicleSale(Base):
    __tablename__ = "vehicle_sales"
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    sale_price = Column(Float, nullable=False)
    gross_profit = Column(Float, nullable=False)
    sale_date = Column(Date, nullable=False)
    primary_salesperson_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("sale_price >= 0"),
        CheckConstraint("gross_profit >= 0"),
    )

# Pydantic Models
class SpiffPlanCreate(BaseModel):
    name: str
    award_amount: float
    effective_start_date: date
    effective_end_date: date

class SpiffPlanResponse(SpiffPlanCreate):
    id: int
    status: str = "active"
    created_by: int

    class Config:
        from_attributes = True

class SpiffPlanConditionCreate(BaseModel):
    spiff_plan_id: int
    condition_type: Literal["gross_profit_threshold", "vehicle_type", "sales_volume", "other"]
    operator: Literal[">", ">=", "<", "<=", "=", "!="]
    threshold_value: float

class SpiffPlanConditionResponse(SpiffPlanConditionCreate):
    id: int

    class Config:
        from_attributes = True

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/spiff_plans", response_model=SpiffPlanResponse, operation_id="create_spiff_plan")
def create_spiff_plan(plan: SpiffPlanCreate):
    db = next(get_db())
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    db_plan = SpiffPlan(
        name=plan.name,
        award_amount=plan.award_amount,
        effective_start_date=plan.effective_start_date,
        effective_end_date=plan.effective_end_date,
        created_by=current_user_id
    )
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    return db_plan

@app.post("/spiff_plan_conditions", response_model=SpiffPlanConditionResponse, operation_id="add_spiff_condition")
def add_spiff_condition(condition: SpiffPlanConditionCreate):
    db = next(get_db())
    
    db_condition = SpiffPlanCondition(
        spiff_plan_id=condition.spiff_plan_id,
        condition_type=condition.condition_type,
        operator=condition.operator,
        threshold_value=condition.threshold_value
    )
    db.add(db_condition)
    db.commit()
    db.refresh(db_condition)
    return db_condition
