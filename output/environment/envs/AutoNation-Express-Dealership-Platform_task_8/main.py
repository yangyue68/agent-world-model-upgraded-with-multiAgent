import os
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class Dealership(Base):
    __tablename__ = "dealerships"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    zip = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True)
    vin = Column(String, nullable=False, unique=True)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    mileage = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
    dealership_id = Column(Integer, ForeignKey("dealerships.id"), nullable=False)
    primary_photo_url = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("price >= 0"),
        CheckConstraint("mileage >= 0"),
        CheckConstraint("status IN ('Available', 'Pending Sale', 'Sold', 'In Transit', 'On Hold')"),
    )

class Feature(Base):
    __tablename__ = "features"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class VehicleFeature(Base):
    __tablename__ = "vehicle_features"
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), primary_key=True)
    feature_id = Column(Integer, ForeignKey("features.id"), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class VehiclePhoto(Base):
    __tablename__ = "vehicle_photos"
    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    url = Column(String, nullable=False)
    is_primary = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("is_primary IN (0, 1)"),
    )

# Request Models
class UpdateVehicleRequest(BaseModel):
    price: Optional[float] = None
    mileage: Optional[int] = None
    status: Optional[str] = None
    dealership_id: Optional[int] = None
    feature_name: Optional[str] = None
    new_primary_photo_url: Optional[str] = None
    old_primary_photo_url: Optional[str] = None

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/dealerships", tags=["PrimaryResource"], operation_id="find_dealerships")
def find_dealerships(name: str):
    db = next(get_db())
    dealerships = db.query(Dealership).filter(Dealership.name.contains(name)).all()
    return [{"id": d.id, "name": d.name} for d in dealerships]

@app.put("/vehicles/{vin}", tags=["PrimaryResource"], operation_id="update_vehicle")
def update_vehicle(vin: str, payload: UpdateVehicleRequest):
    db = next(get_db())
    
    # Find vehicle by VIN
    vehicle = db.query(Vehicle).filter(Vehicle.vin == vin).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Update vehicle fields if provided
    if payload.price is not None:
        vehicle.price = payload.price
    if payload.mileage is not None:
        vehicle.mileage = payload.mileage
    if payload.status is not None:
        vehicle.status = payload.status
    if payload.dealership_id is not None:
        vehicle.dealership_id = payload.dealership_id
    
    # Handle feature addition
    if payload.feature_name:
        feature = db.query(Feature).filter(Feature.name == payload.feature_name).first()
        if not feature:
            raise HTTPException(status_code=400, detail=f"Feature {payload.feature_name} not found")
        
        # Check if feature already exists for this vehicle
        existing = db.query(VehicleFeature).filter(
            VehicleFeature.vehicle_id == vehicle.id,
            VehicleFeature.feature_id == feature.id
        ).first()
        if not existing:
            vf = VehicleFeature(vehicle_id=vehicle.id, feature_id=feature.id)
            db.add(vf)
    
    # Handle photo updates
    if payload.new_primary_photo_url:
        # Set new primary photo
        new_photo = VehiclePhoto(
            vehicle_id=vehicle.id,
            url=payload.new_primary_photo_url,
            is_primary=1
        )
        db.add(new_photo)
        
        # Remove old primary photo if specified
        if payload.old_primary_photo_url:
            old_photo = db.query(VehiclePhoto).filter(
                VehiclePhoto.vehicle_id == vehicle.id,
                VehiclePhoto.url == payload.old_primary_photo_url,
                VehiclePhoto.is_primary == 1
            ).first()
            if old_photo:
                db.delete(old_photo)
        
        # Update vehicle's primary photo URL
        vehicle.primary_photo_url = payload.new_primary_photo_url
    
    # Commit changes
    db.commit()
    
    return {
        "message": "Vehicle updated successfully",
        "vehicle_id": vehicle.id
    }
