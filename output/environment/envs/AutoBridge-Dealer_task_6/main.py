import os
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

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
    full_name = Column(String)
    email = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Deal(Base):
    __tablename__ = "deals"
    
    id = Column(Integer, primary_key=True, index=True)
    deal_number = Column(String, unique=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    status = Column(String, nullable=False, default="active")
    voided_at = Column(DateTime)
    voided_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    customer = relationship("Customer")
    voided_by_user = relationship("User")

class DealAuditLog(Base):
    __tablename__ = "deal_audit_log"
    
    id = Column(Integer, primary_key=True, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id"))
    action = Column(String, nullable=False)
    performed_by = Column(Integer, ForeignKey("users.id"))
    performed_at = Column(DateTime, default=datetime.utcnow)
    details = Column(Text)
    
    deal = relationship("Deal")
    performed_by_user = relationship("User")

# Pydantic models
class DealResponse(BaseModel):
    id: int
    deal_number: str
    status: str
    voided_at: Optional[datetime]
    voided_by: Optional[int]

class DealSearchResult(BaseModel):
    id: int
    deal_number: str
    customer_id: int
    status: str
    customer_first_name: str
    customer_last_name: str

class CustomerSearchResult(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/deals/{deal_id}/void", response_model=DealResponse, operation_id="void_deal")
def void_deal(deal_id: int, db = Depends(get_db)):
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find the deal
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    
    # Check if deal is active
    if deal.status != "active":
        raise HTTPException(status_code=400, detail="Deal is not active and cannot be voided")
    
    # Update deal status and void info
    deal.status = "voided"
    deal.voided_at = datetime.utcnow()
    deal.voided_by = current_user_id
    
    # Add audit log entry
    audit_log = DealAuditLog(
        deal_id=deal.id,
        action="voided",
        performed_by=current_user_id,
        details=f"Deal {deal.deal_number} voided by user {current_user_id}"
    )
    db.add(audit_log)
    
    db.commit()
    db.refresh(deal)
    
    return DealResponse(
        id=deal.id,
        deal_number=deal.deal_number,
        status=deal.status,
        voided_at=deal.voided_at,
        voided_by=deal.voided_by
    )

@app.get("/deals/search", response_model=List[DealSearchResult], operation_id="search_deals")
def search_deals(deal_number: str, db = Depends(get_db)):
    deals = db.query(Deal, Customer.first_name, Customer.last_name)\
        .join(Customer, Deal.customer_id == Customer.id)\
        .filter(Deal.deal_number == deal_number)\
        .all()
    
    results = []
    for deal, first_name, last_name in deals:
        results.append(DealSearchResult(
            id=deal.id,
            deal_number=deal.deal_number,
            customer_id=deal.customer_id,
            status=deal.status,
            customer_first_name=first_name,
            customer_last_name=last_name
        ))
    
    return results

@app.get("/customers/search", response_model=List[CustomerSearchResult], operation_id="search_customers")
def search_customers(first_name: str, last_name: str, db = Depends(get_db)):
    customers = db.query(Customer)\
        .filter(Customer.first_name == first_name, Customer.last_name == last_name)\
        .all()
    
    results = []
    for customer in customers:
        results.append(CustomerSearchResult(
            id=customer.id,
            first_name=customer.first_name,
            last_name=customer.last_name,
            email=customer.email
        ))
    
    return results
