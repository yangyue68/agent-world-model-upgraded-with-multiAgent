from sqlalchemy import Float
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
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    status = Column(String, nullable=False, default="new")
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

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
    price = Column(Float)
    status = Column(String, nullable=False, default="available")
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

# Pydantic Models
class LeadResponse(BaseModel):
    id: int
    first_name: str
    last_name: str

class VehicleResponse(BaseModel):
    id: int
    vin: str
    make: str
    model: str
    year: int

class TestDriveResponse(BaseModel):
    id: int
    lead_id: int
    vehicle_id: int
    scheduled_at: str
    status: str
    created_by: int

class ScheduleTestDriveRequest(BaseModel):
    lead_first_name: str
    lead_last_name: str
    vehicle_vin: str
    scheduled_datetime: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/leads/search", response_model=List[LeadResponse], operation_id="search_leads")
def search_leads(first_name: str, last_name: str, db=None):
    if db is None:
        db = next(get_db())
    
    leads = db.query(Lead).filter(
        Lead.first_name == first_name,
        Lead.last_name == last_name
    ).all()
    
    return leads

@app.get("/vehicles/search", response_model=List[VehicleResponse], operation_id="search_vehicles")
def search_vehicles(vin: str, db=None):
    if db is None:
        db = next(get_db())
    
    vehicles = db.query(Vehicle).filter(Vehicle.vin == vin).all()
    
    return vehicles

@app.post("/test_drives", response_model=TestDriveResponse, operation_id="schedule_test_drive")
def schedule_test_drive(request: ScheduleTestDriveRequest, db=None):
    if db is None:
        db = next(get_db())
    
    # Find lead
    lead = db.query(Lead).filter(
        Lead.first_name == request.lead_first_name,
        Lead.last_name == request.lead_last_name
    ).first()
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Find vehicle
    vehicle = db.query(Vehicle).filter(Vehicle.vin == request.vehicle_vin).first()
    
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Parse scheduled datetime
    try:
        scheduled_at = datetime.fromisoformat(request.scheduled_datetime.replace(' ', 'T'))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format")
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Create test drive
    test_drive = TestDrive(
        lead_id=lead.id,
        vehicle_id=vehicle.id,
        scheduled_at=scheduled_at,
        created_by=current_user_id
    )
    
    db.add(test_drive)
    db.commit()
    db.refresh(test_drive)
    
    # Convert scheduled_at to string for response
    test_drive.scheduled_at = test_drive.scheduled_at.isoformat()
    
    return test_drive
