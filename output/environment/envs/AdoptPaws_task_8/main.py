import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, REAL, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

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
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Cat(Base):
    __tablename__ = "cats"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    breed = Column(String)
    age = Column(Integer)
    description = Column(Text)
    status = Column(String, nullable=False, default="available")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class AdoptionApplication(Base):
    __tablename__ = "adoption_applications"
    
    id = Column(Integer, primary_key=True, index=True)
    application_number = Column(String, unique=True, nullable=False)
    cat_id = Column(Integer, ForeignKey("cats.id"), nullable=False)
    applicant_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=False, default="submitted")
    submitted_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime)
    cancelled_at = Column(DateTime)
    cancellation_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    cat = relationship("Cat")
    applicant = relationship("User")

class Payment(Base):
    __tablename__ = "payments"
    
    id = Column(Integer, primary_key=True, index=True)
    payment_number = Column(String, unique=True, nullable=False)
    application_id = Column(Integer, ForeignKey("adoption_applications.id"))
    payer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(REAL, nullable=False)
    currency = Column(String, nullable=False, default="USD")
    payment_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    payment_date = Column(DateTime, default=datetime.utcnow)
    voided_at = Column(DateTime)
    void_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    application = relationship("AdoptionApplication")
    payer = relationship("User")

# Pydantic Models
class AdoptionApplicationResponse(BaseModel):
    id: int
    application_number: str
    status: str
    cat_id: int
    applicant_id: int

class AdoptionApplicationCancelRequest(BaseModel):
    cancellation_reason: str

class AdoptionApplicationCancelResponse(BaseModel):
    id: int
    application_number: str
    status: str
    cancelled_at: datetime
    cancellation_reason: str

class PaymentResponse(BaseModel):
    id: int
    payment_number: str
    status: str
    amount: float
    currency: str
    payment_type: str
    application_id: Optional[int]
    payer_id: int

class PaymentVoidRequest(BaseModel):
    void_reason: str

class PaymentVoidResponse(BaseModel):
    id: int
    payment_number: str
    status: str
    voided_at: datetime
    void_reason: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/adoption-applications/lookup", response_model=List[AdoptionApplicationResponse], operation_id="lookup_adoption_application")
def lookup_adoption_application(
    application_number: Optional[str] = Query(None),
    cat_name: Optional[str] = Query(None),
    applicant_full_name: Optional[str] = Query(None),
    db = None
):
    if db is None:
        db = next(get_db())
    
    query = db.query(AdoptionApplication)
    
    if application_number:
        query = query.filter(AdoptionApplication.application_number == application_number)
    
    if cat_name:
        query = query.join(Cat).filter(Cat.name == cat_name)
    
    if applicant_full_name:
        query = query.join(User).filter(User.full_name == applicant_full_name)
    
    applications = query.all()
    
    return [
        AdoptionApplicationResponse(
            id=app.id,
            application_number=app.application_number,
            status=app.status,
            cat_id=app.cat_id,
            applicant_id=app.applicant_id
        )
        for app in applications
    ]

@app.post("/adoption-applications/{id}/cancel", response_model=AdoptionApplicationCancelResponse, operation_id="cancel_adoption_application")
def cancel_adoption_application(
    id: int = Path(..., description="The internal database ID of the adoption application to cancel"),
    cancellation_reason: str = Body(..., embed=True),
    db = None
):
    if db is None:
        db = next(get_db())
    
    application = db.query(AdoptionApplication).filter(AdoptionApplication.id == id).first()
    
    if not application:
        raise HTTPException(status_code=404, detail="Adoption application not found")
    
    if application.status not in ["submitted", "under_review"]:
        raise HTTPException(status_code=400, detail="Cannot cancel application in current status")
    
    application.status = "cancelled"
    application.cancelled_at = datetime.utcnow()
    application.cancellation_reason = cancellation_reason
    
    db.commit()
    db.refresh(application)
    
    return AdoptionApplicationCancelResponse(
        id=application.id,
        application_number=application.application_number,
        status=application.status,
        cancelled_at=application.cancelled_at,
        cancellation_reason=application.cancellation_reason
    )

@app.get("/payments/lookup", response_model=List[PaymentResponse], operation_id="lookup_payment")
def lookup_payment(
    payment_number: Optional[str] = Query(None),
    application_id: Optional[int] = Query(None),
    db = None
):
    if db is None:
        db = next(get_db())
    
    query = db.query(Payment)
    
    if payment_number:
        query = query.filter(Payment.payment_number == payment_number)
    
    if application_id:
        query = query.filter(Payment.application_id == application_id)
    
    payments = query.all()
    
    return [
        PaymentResponse(
            id=pay.id,
            payment_number=pay.payment_number,
            status=pay.status,
            amount=pay.amount,
            currency=pay.currency,
            payment_type=pay.payment_type,
            application_id=pay.application_id,
            payer_id=pay.payer_id
        )
        for pay in payments
    ]

@app.post("/payments/{id}/void", response_model=PaymentVoidResponse, operation_id="void_payment")
def void_payment(
    id: int = Path(..., description="The internal database ID of the payment to void"),
    void_reason: str = Body(..., embed=True),
    db = None
):
    if db is None:
        db = next(get_db())
    
    payment = db.query(Payment).filter(Payment.id == id).first()
    
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    if payment.status not in ["pending", "completed"]:
        raise HTTPException(status_code=400, detail="Cannot void payment in current status")
    
    payment.status = "voided"
    payment.voided_at = datetime.utcnow()
    payment.void_reason = void_reason
    
    db.commit()
    db.refresh(payment)
    
    return PaymentVoidResponse(
        id=payment.id,
        payment_number=payment.payment_number,
        status=payment.status,
        voided_at=payment.voided_at,
        void_reason=payment.void_reason
    )
