import os
from datetime import datetime
from typing import Optional, Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    address = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    condition = Column(String, nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    base_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("condition IN ('New', 'Used', 'Certified')"),)

class PricingRule(Base):
    __tablename__ = "pricing_rules"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    markup_percentage = Column(Float, nullable=False)
    condition_filter = Column(String)
    location_id = Column(Integer, ForeignKey("locations.id"))
    is_active = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("markup_percentage >= 0"),
        CheckConstraint("condition_filter IN ('New', 'Used', 'Certified')"),
        CheckConstraint("is_active IN (0, 1)"),
    )

# Request/Response Models
class CreatePricingRuleRequest(BaseModel):
    name: str
    markup_percentage: float
    condition_filter: Literal["New", "Used", "Certified"]
    location_id: Optional[int] = None

class PricingRuleResponse(BaseModel):
    id: int
    name: str
    markup_percentage: float
    condition_filter: Literal["New", "Used", "Certified"]
    location_id: Optional[int]
    is_active: int
    created_by: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/pricing_rules", response_model=PricingRuleResponse, operation_id="create_pricing_rule")
def create_pricing_rule(rule: CreatePricingRuleRequest):
    db = next(get_db())
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if current_user_id == 0:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Create new pricing rule
    db_rule = PricingRule(
        name=rule.name,
        markup_percentage=rule.markup_percentage,
        condition_filter=rule.condition_filter,
        location_id=rule.location_id,
        created_by=current_user_id,
        is_active=1
    )
    
    db.add(db_rule)
    db.commit()
    db.refresh(db_rule)
    
    return PricingRuleResponse(
        id=db_rule.id,
        name=db_rule.name,
        markup_percentage=db_rule.markup_percentage,
        condition_filter=db_rule.condition_filter,
        location_id=db_rule.location_id,
        is_active=db_rule.is_active,
        created_by=db_rule.created_by
    )
