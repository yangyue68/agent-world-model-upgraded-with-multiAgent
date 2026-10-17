import os
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    
    id = Column(Integer, primary_key=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    trim = Column(String)
    price = Column(Float, nullable=False)
    certification_status = Column(String, nullable=False, default="Not Certified")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PriceHistory(Base):
    __tablename__ = "price_history"
    
    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    old_price = Column(Float, nullable=False)
    new_price = Column(Float, nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow)

class CertificationHistory(Base):
    __tablename__ = "certification_history"
    
    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    old_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class UpdateVehiclePriceAndCertificationRequest(BaseModel):
    price: float
    certification_status: Literal["Certified", "Not Certified", "Pending"]

class UpdateVehiclePriceAndCertificationResponse(BaseModel):
    vehicle_id: int
    message: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/vehicles/{vin}", response_model=UpdateVehiclePriceAndCertificationResponse, operation_id="update_vehicle_price_and_certification")
def update_vehicle_price_and_certification(
    vin: str = Path(..., description="Vehicle Identification Number"),
    body: UpdateVehiclePriceAndCertificationRequest = None,
    db = None
):
    if db is None:
        db = next(get_db())
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find vehicle by VIN
    vehicle = db.query(Vehicle).filter(Vehicle.vin == vin).first()
    
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Record price change if needed
    if vehicle.price != body.price:
        old_price = vehicle.price
        new_price = body.price
        
        # Create price history entry
        price_history_entry = PriceHistory(
            vehicle_id=vehicle.id,
            old_price=old_price,
            new_price=new_price,
            changed_by=current_user_id
        )
        db.add(price_history_entry)
        
        # Update vehicle price
        vehicle.price = new_price
    
    # Record certification status change if needed
    if vehicle.certification_status != body.certification_status:
        old_status = vehicle.certification_status
        new_status = body.certification_status
        
        # Create certification history entry
        cert_history_entry = CertificationHistory(
            vehicle_id=vehicle.id,
            old_status=old_status,
            new_status=new_status,
            changed_by=current_user_id
        )
        db.add(cert_history_entry)
        
        # Update vehicle certification status
        vehicle.certification_status = new_status
    
    # Commit changes
    db.commit()
    
    return UpdateVehiclePriceAndCertificationResponse(
        vehicle_id=vehicle.id,
        message=f"Vehicle {vin} updated successfully"
    )
