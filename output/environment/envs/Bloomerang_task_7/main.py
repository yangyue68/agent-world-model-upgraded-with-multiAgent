import os
from datetime import date
from typing import Optional
from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, Text, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

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
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class Constituent(Base):
    __tablename__ = "constituents"
    id = Column(String, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text)
    start_date = Column(Date)
    end_date = Column(Date)
    goal_amount = Column(Float)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class Fund(Base):
    __tablename__ = "funds"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class Appeal(Base):
    __tablename__ = "appeals"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class PaymentMethod(Base):
    __tablename__ = "payment_methods"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(Text)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class Donation(Base):
    __tablename__ = "donations"
    id = Column(Integer, primary_key=True, index=True)
    constituent_id = Column(String, ForeignKey("constituents.id"), nullable=False)
    amount = Column(Float, nullable=False)
    donation_date = Column(Date, nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    fund_id = Column(Integer, ForeignKey("funds.id"))
    appeal_id = Column(Integer, ForeignKey("appeals.id"))
    payment_method_id = Column(Integer, ForeignKey("payment_methods.id"))
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, default=1)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    
    __table_args__ = (
        CheckConstraint("amount > 0", name="check_amount_positive"),
    )

# Request and Response Models
class DonationAssignmentRequest(BaseModel):
    campaign_id: Optional[int] = None
    fund_id: Optional[int] = None
    appeal_id: Optional[int] = None
    payment_method_id: Optional[int] = None

class DonationResponse(BaseModel):
    id: int
    constituent_id: str
    amount: float
    campaign_id: Optional[int]
    fund_id: Optional[int]
    appeal_id: Optional[int]
    payment_method_id: Optional[int]

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/donations/{donation_id}", response_model=DonationResponse, operation_id="update_donation_assignment")
def update_donation_assignment(
    donation_id: int,
    assignment: DonationAssignmentRequest,
    db: Session = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    
    db_donation = db.query(Donation).filter(Donation.id == donation_id).first()
    if not db_donation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Donation not found")
    
    # Verify the donation belongs to the correct constituent
    if assignment.campaign_id is not None:
        db_donation.campaign_id = assignment.campaign_id
    if assignment.fund_id is not None:
        db_donation.fund_id = assignment.fund_id
    if assignment.appeal_id is not None:
        db_donation.appeal_id = assignment.appeal_id
    if assignment.payment_method_id is not None:
        db_donation.payment_method_id = assignment.payment_method_id
    
    db_donation.created_by = current_user_id
    
    db.commit()
    db.refresh(db_donation)
    
    return db_donation
