import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint, func, case
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
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    role = Column(String, default="salesperson", nullable=False)
    is_active = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    phone = Column(String)
    address = Column(Text)
    city = Column(String)
    state = Column(String)
    postal_code = Column(String)
    notes = Column(Text)
    is_active = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    trim = Column(String)
    color = Column(String)
    mileage = Column(Integer)
    price = Column(String)  # Changed from float to String to match schema
    status = Column(String, default="available", nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class TestDrive(Base):
    __tablename__ = "test_drives"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    salesperson_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    duration_minutes = Column(Integer, default=30, nullable=False)
    status = Column(String, default="scheduled", nullable=False)
    notes = Column(Text)
    cancelled_at = Column(DateTime)
    cancelled_by = Column(Integer, ForeignKey("users.id"))
    cancellation_reason = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    customer = relationship("Customer")
    vehicle = relationship("Vehicle")
    salesperson = relationship("User", foreign_keys=[salesperson_id])

class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(String, default="system", nullable=False)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    related_table = Column(String)
    related_id = Column(Integer)
    is_read = Column(Integer, default=0, nullable=False)
    read_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# Pydantic Models
class VehicleResponse(BaseModel):
    id: int
    make: str
    model: str
    year: int

    class Config:
        from_attributes = True

class SalespersonResponse(BaseModel):
    id: int
    full_name: str

    class Config:
        from_attributes = True

class TestDriveResponse(BaseModel):
    id: int
    customer_id: int
    vehicle_id: int
    salesperson_id: int
    scheduled_at: datetime
    duration_minutes: int
    status: str
    vehicle: VehicleResponse
    salesperson: SalespersonResponse

    class Config:
        from_attributes = True

class CancelTestDriveRequest(BaseModel):
    cancellation_reason: str

class CancelTestDriveResponse(BaseModel):
    id: int
    status: str
    cancelled_at: datetime
    cancelled_by: int
    cancellation_reason: str

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

@app.get("/test_drives", response_model=List[TestDriveResponse], operation_id="find_test_drives")
def find_test_drives(
    customer_first_name: str = Query(...),
    customer_last_name: str = Query(...),
    date: str = Query(...),
    db = None
):
    if db is None:
        db = next(get_db())
    
    # Parse date string to datetime object
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")
    
    # Find customer
    customer = db.query(Customer).filter(
        Customer.first_name == customer_first_name,
        Customer.last_name == customer_last_name
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    # Find test drives for the customer on the specified date
    start_of_day = datetime.combine(parsed_date, datetime.min.time())
    end_of_day = datetime.combine(parsed_date, datetime.max.time())
    
    test_drives = db.query(TestDrive).filter(
        TestDrive.customer_id == customer.id,
        TestDrive.scheduled_at >= start_of_day,
        TestDrive.scheduled_at <= end_of_day
    ).all()
    
    # Convert to response format
    result = []
    for td in test_drives:
        vehicle_data = VehicleResponse.model_validate(td.vehicle)
        salesperson_data = SalespersonResponse.model_validate(td.salesperson)
        
        response = TestDriveResponse(
            id=td.id,
            customer_id=td.customer_id,
            vehicle_id=td.vehicle_id,
            salesperson_id=td.salesperson_id,
            scheduled_at=td.scheduled_at,
            duration_minutes=td.duration_minutes,
            status=td.status,
            vehicle=vehicle_data,
            salesperson=salesperson_data
        )
        result.append(response)
    
    return result

@app.put("/test_drives/{id}/cancel", response_model=CancelTestDriveResponse, operation_id="cancel_test_drive")
def cancel_test_drive(
    id: int,
    body: CancelTestDriveRequest,
    db = None
):
    if db is None:
        db = next(get_db())
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    cancellation_reason = body.cancellation_reason
    
    # Find the test drive
    test_drive = db.query(TestDrive).filter(TestDrive.id == id).first()
    
    if not test_drive:
        raise HTTPException(status_code=404, detail="Test drive not found")
    
    if test_drive.status != "scheduled":
        raise HTTPException(status_code=400, detail="Only scheduled test drives can be cancelled")
    
    # Update test drive
    test_drive.status = "cancelled"
    test_drive.cancelled_at = datetime.utcnow()
    test_drive.cancelled_by = current_user_id
    test_drive.cancellation_reason = cancellation_reason
    
    # Update vehicle status back to available
    vehicle = db.query(Vehicle).filter(Vehicle.id == test_drive.vehicle_id).first()
    if vehicle:
        vehicle.status = "available"
    
    # Create notification for salesperson
    notification = Notification(
        user_id=test_drive.salesperson_id,
        type="test_drive",
        title="Test Drive Cancelled",
        message=f"Test drive for {test_drive.customer.first_name} {test_drive.customer.last_name} with {test_drive.vehicle.make} {test_drive.vehicle.model} has been cancelled.",
        related_table="test_drives",
        related_id=test_drive.id
    )
    db.add(notification)
    
    db.commit()
    db.refresh(test_drive)
    
    # Prepare response
    response = CancelTestDriveResponse(
        id=test_drive.id,
        status=test_drive.status,
        cancelled_at=test_drive.cancelled_at,
        cancelled_by=test_drive.cancelled_by,
        cancellation_reason=test_drive.cancellation_reason
    )
    
    return response
