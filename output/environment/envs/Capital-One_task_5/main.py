import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models matching the existing schema
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    account_type = Column(String, nullable=False)
    account_number_last4 = Column(String, nullable=False)
    account_name = Column(String)
    balance = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Payee(Base):
    __tablename__ = "payees"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    account_number = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('user_id', 'name', 'account_number'),
    )

class ScheduledPayment(Base):
    __tablename__ = "scheduled_payments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    payee_id = Column(Integer, ForeignKey("payees.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    amount = Column(Float, nullable=False)
    frequency = Column(String, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date)
    next_payment_date = Column(Date)
    status = Column(String, nullable=False, default='active')
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Pydantic Schemas
class PayeeCreate(BaseModel):
    name: str
    account_number: str

class PayeeResponse(BaseModel):
    id: int
    name: str
    account_number: str
    
    class Config:
        from_attributes = True

class ScheduledPaymentCreate(BaseModel):
    payee_id: int
    account_id: int
    amount: float
    frequency: str
    start_date: date

class ScheduledPaymentResponse(BaseModel):
    id: int
    payee_id: int
    account_id: int
    amount: float
    frequency: str
    start_date: date
    status: str
    
    class Config:
        from_attributes = True

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Helper to get current user ID from environment
def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

app = FastAPI(title="Bill Pay API")

@app.post("/payees", response_model=PayeeResponse, operation_id="create_payee")
def create_payee(
    payee_in: PayeeCreate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id)
):
    # Check for duplicate payee for this user
    existing_payee = db.query(Payee).filter(
        Payee.user_id == current_user_id,
        Payee.name == payee_in.name,
        Payee.account_number == payee_in.account_number
    ).first()
    
    if existing_payee:
        raise HTTPException(status_code=400, detail="Payee already exists")

    new_payee = Payee(
        user_id=current_user_id,
        name=payee_in.name,
        account_number=payee_in.account_number
    )
    db.add(new_payee)
    db.commit()
    db.refresh(new_payee)
    
    return new_payee

@app.post("/scheduled_payments", response_model=ScheduledPaymentResponse, operation_id="schedule_payment")
def schedule_payment(
    payment_in: ScheduledPaymentCreate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id)
):
    # Validate frequency
    allowed_frequencies = ['one-time', 'weekly', 'biweekly', 'monthly', 'quarterly', 'annually']
    if payment_in.frequency not in allowed_frequencies:
        raise HTTPException(status_code=400, detail=f"Invalid frequency. Must be one of {allowed_frequencies}")

    # Verify payee belongs to current user
    payee = db.query(Payee).filter(
        Payee.id == payment_in.payee_id,
        Payee.user_id == current_user_id
    ).first()
    if not payee:
        raise HTTPException(status_code=404, detail="Payee not found or does not belong to user")

    # Verify account belongs to current user
    account = db.query(Account).filter(
        Account.id == payment_in.account_id,
        Account.user_id == current_user_id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found or does not belong to user")

    # Calculate next_payment_date based on start_date and frequency
    # For simplicity, we assume next_payment_date starts as start_date unless logic dictates otherwise
    # The spec doesn't explicitly define how next_payment_date is calculated relative to start_date 
    # other than it being a recurring schedule. Usually, the first payment is on start_date.
    next_payment_date_val = payment_in.start_date

    new_scheduled_payment = ScheduledPayment(
        user_id=current_user_id,
        payee_id=payment_in.payee_id,
        account_id=payment_in.account_id,
        amount=payment_in.amount,
        frequency=payment_in.frequency,
        start_date=payment_in.start_date,
        next_payment_date=next_payment_date_val,
        status='active'
    )
    
    db.add(new_scheduled_payment)
    db.commit()
    db.refresh(new_scheduled_payment)
    
    return new_scheduled_payment
