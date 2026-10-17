import os
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class Vehicle(Base):
    __tablename__ = "vehicles"
    
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    mileage = Column(Integer, nullable=False, default=0)
    condition = Column(String, nullable=False, default="Used")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class VehicleUpdate(Base):
    __tablename__ = "vehicle_updates"
    
    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    user_id = Column(Integer, nullable=False)
    field_name = Column(String, nullable=False)
    old_value = Column(Text)
    new_value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Request and Response Models
class UpdateVehicleRequest(BaseModel):
    mileage: int
    condition: Literal["New", "Used", "Certified Pre-Owned"]

class VehicleResponse(BaseModel):
    id: int
    vin: str
    make: str
    model: str
    year: int
    mileage: int
    condition: str
    updated_at: datetime

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/vehicles/{vin}", response_model=VehicleResponse, operation_id="update_vehicle_mileage_and_condition")
def update_vehicle_mileage_and_condition(
    vin: str,
    update_data: UpdateVehicleRequest,
    db: Session = Depends(get_db)
):
    # Get the current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find the vehicle by VIN
    vehicle = db.query(Vehicle).filter(Vehicle.vin == vin).first()
    
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Store old values for audit trail
    old_mileage = vehicle.mileage
    old_condition = vehicle.condition
    
    # Update the vehicle
    vehicle.mileage = update_data.mileage
    vehicle.condition = update_data.condition
    vehicle.updated_at = datetime.utcnow()
    
    # Record the update in vehicle_updates table
    vehicle_update_mileage = VehicleUpdate(
        vehicle_id=vehicle.id,
        user_id=current_user_id,
        field_name="mileage",
        old_value=str(old_mileage),
        new_value=str(update_data.mileage),
        updated_at=datetime.utcnow()
    )
    db.add(vehicle_update_mileage)
    
    vehicle_update_condition = VehicleUpdate(
        vehicle_id=vehicle.id,
        user_id=current_user_id,
        field_name="condition",
        old_value=old_condition,
        new_value=update_data.condition,
        updated_at=datetime.utcnow()
    )
    db.add(vehicle_update_condition)
    
    db.commit()
    db.refresh(vehicle)
    
    return vehicle
