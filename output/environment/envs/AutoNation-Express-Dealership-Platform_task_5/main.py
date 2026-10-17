import os
from datetime import datetime
from typing import Optional, Generator
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
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
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    address_line1 = Column(String)
    address_line2 = Column(String)
    city = Column(String)
    state = Column(String)
    zip_code = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Dealership(Base):
    __tablename__ = "dealerships"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address_line1 = Column(String)
    address_line2 = Column(String)
    city = Column(String)
    state = Column(String)
    zip_code = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True, index=True)
    dealership_id = Column(Integer, ForeignKey("dealerships.id"), nullable=False)
    stock_number = Column(String, nullable=False)
    vin = Column(String, unique=True)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    trim = Column(String)
    model_year = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    mileage = Column(Integer, default=0)
    exterior_color = Column(String)
    interior_color = Column(String)
    transmission = Column(String)
    fuel_type = Column(String)
    body_style = Column(String)
    status = Column(String, nullable=False, default="available")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class LoanApplication(Base):
    __tablename__ = "loan_applications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True)
    requested_amount = Column(Float, nullable=False)
    down_payment = Column(Float, nullable=False)
    term_months = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")
    approved_amount = Column(Float)
    interest_rate = Column(Float)
    monthly_payment = Column(Float)
    decision_date = Column(DateTime)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Reservation(Base):
    __tablename__ = "reservations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    loan_application_id = Column(Integer, ForeignKey("loan_applications.id"), nullable=True)
    reservation_amount = Column(Float, default=500.0)
    status = Column(String, nullable=False, default="active")
    refundable = Column(Integer, default=1)
    reserved_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)
    cancelled_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Pydantic Models
class LoanApplicationRequest(BaseModel):
    requested_amount: float
    down_payment: float
    term_months: int

class ReservationRequest(BaseModel):
    vehicle_id: int
    loan_application_id: int

class VehicleResponse(BaseModel):
    id: int
    stock_number: str
    make: str
    model: str
    model_year: int
    price: float
    status: str

class LoanApplicationResponse(BaseModel):
    id: int
    status: str
    requested_amount: float
    down_payment: float
    term_months: int

class ReservationResponse(BaseModel):
    id: int
    status: str
    reservation_amount: float
    vehicle_id: int
    loan_application_id: int

class LoanStatusResponse(BaseModel):
    id: int
    status: str
    approved_amount: Optional[float]
    interest_rate: Optional[float]
    monthly_payment: Optional[float]

# FastAPI App
app = FastAPI()

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/loan-applications", response_model=LoanApplicationResponse, operation_id="apply_for_loan")
def apply_for_loan(loan_request: LoanApplicationRequest, db: Session = Depends(get_db)):
    user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Create new loan application
    loan_application = LoanApplication(
        user_id=user_id,
        requested_amount=loan_request.requested_amount,
        down_payment=loan_request.down_payment,
        term_months=loan_request.term_months,
        status="pending"
    )
    
    db.add(loan_application)
    db.commit()
    db.refresh(loan_application)
    
    return LoanApplicationResponse(
        id=loan_application.id,
        status=loan_application.status,
        requested_amount=loan_application.requested_amount,
        down_payment=loan_application.down_payment,
        term_months=loan_application.term_months
    )

@app.post("/reservations", response_model=ReservationResponse, operation_id="place_reservation")
def place_reservation(reservation_request: ReservationRequest, db: Session = Depends(get_db)):
    user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify loan application exists and is approved
    loan_app = db.query(LoanApplication).filter(
        LoanApplication.id == reservation_request.loan_application_id,
        LoanApplication.user_id == user_id,
        LoanApplication.status == "approved"
    ).first()
    
    if not loan_app:
        raise HTTPException(status_code=400, detail="Invalid or non-approved loan application")
    
    # Verify vehicle exists and is available
    vehicle = db.query(Vehicle).filter(
        Vehicle.id == reservation_request.vehicle_id,
        Vehicle.status == "available"
    ).first()
    
    if not vehicle:
        raise HTTPException(status_code=400, detail="Vehicle not available for reservation")
    
    # Create reservation
    reservation = Reservation(
        user_id=user_id,
        vehicle_id=reservation_request.vehicle_id,
        loan_application_id=reservation_request.loan_application_id,
        reservation_amount=500.0,
        status="active",
        refundable=1
    )
    
    db.add(reservation)
    db.commit()
    db.refresh(reservation)
    
    # Update vehicle status to reserved
    vehicle.status = "reserved"
    db.commit()
    
    return ReservationResponse(
        id=reservation.id,
        status=reservation.status,
        reservation_amount=reservation.reservation_amount,
        vehicle_id=reservation.vehicle_id,
        loan_application_id=reservation.loan_application_id
    )

@app.get("/vehicles/{vehicle_id}", response_model=VehicleResponse, operation_id="get_vehicle_details")
def get_vehicle_details(vehicle_id: int, db: Session = Depends(get_db)):
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    return VehicleResponse(
        id=vehicle.id,
        stock_number=vehicle.stock_number,
        make=vehicle.make,
        model=vehicle.model,
        model_year=vehicle.model_year,
        price=vehicle.price,
        status=vehicle.status
    )

@app.get("/loan-applications/{application_id}", response_model=LoanStatusResponse, operation_id="check_loan_status")
def check_loan_status(application_id: int, db: Session = Depends(get_db)):
    user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    loan_app = db.query(LoanApplication).filter(
        LoanApplication.id == application_id,
        LoanApplication.user_id == user_id
    ).first()
    
    if not loan_app:
        raise HTTPException(status_code=404, detail="Loan application not found")
    
    return LoanStatusResponse(
        id=loan_app.id,
        status=loan_app.status,
        approved_amount=loan_app.approved_amount,
        interest_rate=loan_app.interest_rate,
        monthly_payment=loan_app.monthly_payment
    )
