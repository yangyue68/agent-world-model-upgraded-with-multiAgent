import os
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint, func
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
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String)
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

class TestDrive(Base):
    __tablename__ = "test_drives"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="scheduled")
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add check constraint for status
    __table_args__ = (
        CheckConstraint("status IN ('scheduled', 'completed', 'cancelled', 'no_show')", name="test_drives_status_check"),
    )

# Pydantic models
class LeadResponse(BaseModel):
    id: int
    name: str

class VehicleResponse(BaseModel):
    id: int
    make: str
    model: str
    year: int

class TestDriveResponse(BaseModel):
    id: int
    lead_id: int
    vehicle_id: int
    scheduled_at: str
    status: str

class TestDriveUpdateRequest(BaseModel):
    id: int
    status: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/leads", response_model=List[LeadResponse], operation_id="find_leads")
def find_leads(name: str, db = Depends(get_db)):
    leads = db.query(Lead).filter(Lead.name.contains(name)).all()
    return leads

@app.get("/vehicles", response_model=List[VehicleResponse], operation_id="find_vehicles")
def find_vehicles(make: str, model: str, year: int, db = Depends(get_db)):
    vehicles = db.query(Vehicle).filter(
        Vehicle.make == make,
        Vehicle.model == model,
        Vehicle.year == year
    ).all()
    return vehicles

@app.get("/test_drives", response_model=List[TestDriveResponse], operation_id="find_test_drives")
def find_test_drives(lead_id: int, vehicle_id: int, scheduled_date: str, db = Depends(get_db)):
    # Handle "tomorrow" date
    if scheduled_date == "tomorrow":
        scheduled_datetime = datetime.now() + timedelta(days=1)
        scheduled_datetime = scheduled_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        # Parse the scheduled_date to datetime
        scheduled_datetime = datetime.strptime(scheduled_date, "%Y-%m-%d")
    
    # Find test drives for the specific date
    end_of_day = scheduled_datetime.replace(hour=23, minute=59, second=59)
    
    test_drives = db.query(TestDrive).filter(
        TestDrive.lead_id == lead_id,
        TestDrive.vehicle_id == vehicle_id,
        TestDrive.scheduled_at >= scheduled_datetime,
        TestDrive.scheduled_at <= end_of_day
    ).all()
    return test_drives

@app.put("/test_drives/{id}", response_model=TestDriveResponse, operation_id="cancel_test_drive")
def cancel_test_drive(id: int, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    test_drive = db.query(TestDrive).filter(TestDrive.id == id).first()
    if not test_drive:
        raise HTTPException(status_code=404, detail="Test drive not found")
    
    # Update the status to cancelled
    test_drive.status = "cancelled"
    test_drive.updated_at = datetime.utcnow()
    test_drive.created_by = current_user_id
    
    db.commit()
    db.refresh(test_drive)
    
    return test_drive
