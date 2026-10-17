import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True, index=True)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    vin = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Deal(Base):
    __tablename__ = "deals"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    status = Column(String, default="Open", server_default="Open")
    sale_price = Column(Float)
    discount_amount = Column(Float, default=0.0)
    discount_approved_by = Column(Integer, ForeignKey("users.id"))
    closed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("status IN ('Open', 'Closed Won', 'Closed Lost')", name="check_status"),
    )

app = FastAPI()

class DealUpdateRequest(BaseModel):
    status: str
    sale_price: float
    discount_amount: float
    discount_approved_by: int

class DealResponse(BaseModel):
    id: int
    customer_id: int
    vehicle_id: int
    status: str
    sale_price: Optional[float]
    discount_amount: float
    discount_approved_by: Optional[int]
    closed_at: Optional[str]

@app.put("/deals/{deal_id}", response_model=DealResponse, operation_id="update_deal_status_and_price")
def update_deal_status_and_price(deal_id: int, deal_update: DealUpdateRequest):
    db = SessionLocal()
    try:
        deal = db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")
        
        if deal_update.status != "Closed Won":
            raise HTTPException(status_code=400, detail="Status must be 'Closed Won'")
        
        # Validate that the discount was approved by a manager
        approver = db.query(User).filter(User.id == deal_update.discount_approved_by).first()
        if not approver:
            raise HTTPException(status_code=400, detail="Invalid discount approver")
        
        # Update deal
        deal.status = deal_update.status
        deal.sale_price = deal_update.sale_price
        deal.discount_amount = deal_update.discount_amount
        deal.discount_approved_by = deal_update.discount_approved_by
        deal.closed_at = datetime.utcnow()
        
        db.commit()
        db.refresh(deal)
        
        return DealResponse(
            id=deal.id,
            customer_id=deal.customer_id,
            vehicle_id=deal.vehicle_id,
            status=deal.status,
            sale_price=deal.sale_price,
            discount_amount=deal.discount_amount,
            discount_approved_by=deal.discount_approved_by,
            closed_at=deal.closed_at.isoformat() if deal.closed_at else None
        )
    finally:
        db.close()
