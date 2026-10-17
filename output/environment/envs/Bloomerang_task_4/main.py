import os
from datetime import date, datetime
from typing import Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, Text, ForeignKey, DateTime, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Constituent(Base):
    __tablename__ = "constituents"
    id = Column(String, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    start_date = Column(Date)
    end_date = Column(Date)
    goal_amount = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Fund(Base):
    __tablename__ = "funds"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Appeal(Base):
    __tablename__ = "appeals"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    start_date = Column(Date)
    end_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PaymentMethod(Base):
    __tablename__ = "payment_methods"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Donation(Base):
    __tablename__ = "donations"
    id = Column(Integer, primary_key=True)
    constituent_id = Column(String, ForeignKey("constituents.id"), nullable=False)
    amount = Column(Float, nullable=False)
    donation_date = Column(Date, nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    fund_id = Column(Integer, ForeignKey("funds.id"))
    appeal_id = Column(Integer, ForeignKey("appeals.id"))
    payment_method_id = Column(Integer, ForeignKey("payment_methods.id"))
    status = Column(String, nullable=False)
    receipt_date = Column(Date)
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("amount > 0", name="check_amount_positive"),
        CheckConstraint("status IN ('Pending', 'Processed', 'Failed', 'Refunded')", name="check_status_enum"),
    )

# Request and response models
class DonationRequest(BaseModel):
    constituent_id: str
    amount: float
    donation_date: date
    campaign_id: int
    fund_id: int
    appeal_id: int
    payment_method_id: int
    status: Literal["Pending", "Processed", "Failed", "Refunded"]
    receipt_date: date

class DonationResponse(DonationRequest):
    id: int
    created_by: int
    created_at: str
    updated_at: str

# Create app
app = FastAPI()

@app.post("/donations", response_model=DonationResponse, operation_id="create_donation")
def create_donation(request: DonationRequest):
    db = SessionLocal()
    try:
        # Get current user from environment
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
        
        # Create donation object
        donation = Donation(
            constituent_id=request.constituent_id,
            amount=request.amount,
            donation_date=request.donation_date,
            campaign_id=request.campaign_id,
            fund_id=request.fund_id,
            appeal_id=request.appeal_id,
            payment_method_id=request.payment_method_id,
            status=request.status,
            receipt_date=request.receipt_date,
            created_by=current_user_id
        )
        
        # Add to database
        db.add(donation)
        db.commit()
        db.refresh(donation)
        
        # Return response
        return DonationResponse(
            id=donation.id,
            constituent_id=donation.constituent_id,
            amount=donation.amount,
            donation_date=donation.donation_date,
            campaign_id=donation.campaign_id,
            fund_id=donation.fund_id,
            appeal_id=donation.appeal_id,
            payment_method_id=donation.payment_method_id,
            status=donation.status,
            receipt_date=donation.receipt_date,
            created_by=donation.created_by,
            created_at=donation.created_at.isoformat() if donation.created_at else None,
            updated_at=donation.updated_at.isoformat() if donation.updated_at else None
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()
