import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class Dealership(Base):
    __tablename__ = "dealerships"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    zip = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    dealership_id = Column(Integer, ForeignKey("dealerships.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    stock_number = Column(String, unique=True, nullable=False)
    dealership_id = Column(Integer, ForeignKey("dealerships.id"), nullable=False)
    year = Column(Integer, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    trim = Column(String)
    price = Column(Float, nullable=False)
    mileage = Column(Integer, nullable=False)
    body_style = Column(String)
    fuel_type = Column(String)
    transmission = Column(String)
    exterior_color = Column(String)
    interior_color = Column(String)
    status = Column(String, default="Available")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    dealership = relationship("Dealership")
    created_by_user = relationship("User")

class Feature(Base):
    __tablename__ = "features"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class VehicleFeature(Base):
    __tablename__ = "vehicle_features"
    
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), primary_key=True)
    feature_id = Column(Integer, ForeignKey("features.id"), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class VehiclePhoto(Base):
    __tablename__ = "vehicle_photos"
    
    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    url = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

# Request and Response Models
class CreateVehicleRequest(BaseModel):
    vin: str
    stock_number: str
    dealership_id: int
    year: int
    make: str
    model: str
    trim: Optional[str] = None
    price: float
    mileage: int
    body_style: Optional[str] = None
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    exterior_color: Optional[str] = None
    interior_color: Optional[str] = None
    features: Optional[List[str]] = []
    photo_urls: Optional[List[str]] = []

class VehicleResponse(BaseModel):
    id: int
    vin: str
    stock_number: str
    dealership_id: int
    year: int
    make: str
    model: str
    trim: Optional[str] = None
    price: float
    mileage: int
    body_style: Optional[str] = None
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    exterior_color: Optional[str] = None
    interior_color: Optional[str] = None
    status: str
    created_at: str
    updated_at: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/vehicles", response_model=VehicleResponse, operation_id="create_used_vehicle_listing")
def create_used_vehicle_listing(request: CreateVehicleRequest):
    db = next(get_db())
    
    # Get current user from environment
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Check if dealership exists
    dealership = db.query(Dealership).filter(Dealership.id == request.dealership_id).first()
    if not dealership:
        raise HTTPException(status_code=400, detail="Invalid dealership ID")
    
    # Check if features exist, create them if needed
    feature_ids = []
    if request.features:
        for feature_name in request.features:
            feature = db.query(Feature).filter(Feature.name == feature_name).first()
            if not feature:
                feature = Feature(name=feature_name)
                db.add(feature)
                db.flush()
            feature_ids.append(feature.id)
    
    # Create vehicle
    vehicle = Vehicle(
        vin=request.vin,
        stock_number=request.stock_number,
        dealership_id=request.dealership_id,
        year=request.year,
        make=request.make,
        model=request.model,
        trim=request.trim,
        price=request.price,
        mileage=request.mileage,
        body_style=request.body_style,
        fuel_type=request.fuel_type,
        transmission=request.transmission,
        exterior_color=request.exterior_color,
        interior_color=request.interior_color,
        status="Available",
        created_by=int(current_user_id)
    )
    
    db.add(vehicle)
    db.flush()  # Get the vehicle ID
    
    # Add vehicle features
    for feature_id in feature_ids:
        vehicle_feature = VehicleFeature(vehicle_id=vehicle.id, feature_id=feature_id)
        db.add(vehicle_feature)
    
    # Add vehicle photos
    if request.photo_urls:
        for i, url in enumerate(request.photo_urls):
            photo = VehiclePhoto(vehicle_id=vehicle.id, url=url, sort_order=i+1)
            db.add(photo)
    
    db.commit()
    db.refresh(vehicle)
    
    # Convert datetime to string for response
    return VehicleResponse(
        id=vehicle.id,
        vin=vehicle.vin,
        stock_number=vehicle.stock_number,
        dealership_id=vehicle.dealership_id,
        year=vehicle.year,
        make=vehicle.make,
        model=vehicle.model,
        trim=vehicle.trim,
        price=vehicle.price,
        mileage=vehicle.mileage,
        body_style=vehicle.body_style,
        fuel_type=vehicle.fuel_type,
        transmission=vehicle.transmission,
        exterior_color=vehicle.exterior_color,
        interior_color=vehicle.interior_color,
        status=vehicle.status,
        created_at=vehicle.created_at.isoformat(),
        updated_at=vehicle.updated_at.isoformat()
    )
