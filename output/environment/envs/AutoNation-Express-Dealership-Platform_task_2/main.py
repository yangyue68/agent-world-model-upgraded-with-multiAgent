import os
from datetime import datetime
from typing import List, Optional, Literal
from fastapi import FastAPI, Query, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# Database setup
DATABASE_PATH = os.getenv("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Dealership(Base):
    __tablename__ = "dealerships"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address_line1 = Column(String, nullable=False)
    address_line2 = Column(String)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    postal_code = Column(String, nullable=False)
    phone = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    stock_number = Column(String, unique=True)
    dealership_id = Column(Integer, ForeignKey("dealerships.id"), nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    trim = Column(String)
    model_year = Column(Integer, CheckConstraint("model_year >= 1900 AND model_year <= 2100"))
    body_style = Column(String, nullable=False)
    drivetrain = Column(String, nullable=False)
    transmission = Column(String)
    fuel_type = Column(String)
    exterior_color = Column(String)
    interior_color = Column(String)
    mileage = Column(Integer, CheckConstraint("mileage >= 0"))
    price = Column(Float, CheckConstraint("price >= 0"))
    third_row_seating = Column(Integer, CheckConstraint("third_row_seating IN (0,1)"), default=0)
    status = Column(String, CheckConstraint("status IN ('available','pending','sold','in_transit')"), default="available")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    dealership = relationship("Dealership")
    features = relationship("VehicleFeature", back_populates="vehicle")
    photos = relationship("VehiclePhoto", back_populates="vehicle")
    specifications = relationship("VehicleSpecification", back_populates="vehicle")
    history_summary = relationship("VehicleHistorySummary", uselist=False, back_populates="vehicle")

class VehicleFeature(Base):
    __tablename__ = "vehicle_features"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    feature_name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    vehicle = relationship("Vehicle", back_populates="features")

class VehiclePhoto(Base):
    __tablename__ = "vehicle_photos"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    photo_url = Column(String, nullable=False)
    caption = Column(String)
    display_order = Column(Integer, default=0)
    is_primary = Column(Integer, CheckConstraint("is_primary IN (0,1)"), default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    vehicle = relationship("Vehicle", back_populates="photos")

class VehicleSpecification(Base):
    __tablename__ = "vehicle_specifications"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    spec_key = Column(String, nullable=False)
    spec_value = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    vehicle = relationship("Vehicle", back_populates="specifications")

class VehicleHistorySummary(Base):
    __tablename__ = "vehicle_history_summaries"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), unique=True, nullable=False)
    provider = Column(String, default="CARFAX")
    report_url = Column(String)
    accident_count = Column(Integer, CheckConstraint("accident_count >= 0"), default=0)
    owner_count = Column(Integer, CheckConstraint("owner_count >= 0"), default=0)
    service_record_count = Column(Integer, CheckConstraint("service_record_count >= 0"), default=0)
    has_damage_history = Column(Integer, CheckConstraint("has_damage_history IN (0,1)"), default=0)
    has_frame_damage = Column(Integer, CheckConstraint("has_frame_damage IN (0,1)"), default=0)
    is_clean_title = Column(Integer, CheckConstraint("is_clean_title IN (0,1)"), default=1)
    summary_text = Column(Text)
    last_reported_date = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    vehicle = relationship("Vehicle", back_populates="history_summary")

# Pydantic models
class VehiclePhotoResponse(BaseModel):
    id: int
    photo_url: str
    caption: str
    is_primary: int

class VehicleSpecificationResponse(BaseModel):
    spec_key: str
    spec_value: str

class VehicleHistorySummaryResponse(BaseModel):
    provider: str
    report_url: str
    accident_count: int
    owner_count: int
    service_record_count: int
    has_damage_history: int
    has_frame_damage: int
    is_clean_title: int
    summary_text: str
    last_reported_date: str

class DealerResponse(BaseModel):
    id: int
    name: str
    address_line1: str
    address_line2: str
    city: str
    state: str
    postal_code: str
    phone: str
    latitude: float
    longitude: float

class VehicleDetailsResponse(BaseModel):
    id: int
    vin: str
    stock_number: str
    make: str
    model: str
    trim: str
    model_year: int
    body_style: str
    drivetrain: str
    transmission: str
    fuel_type: str
    exterior_color: str
    interior_color: str
    mileage: int
    price: float
    third_row_seating: int
    status: str
    photos: List[VehiclePhotoResponse]
    specifications: List[VehicleSpecificationResponse]
    features: List[str]
    history_summary: VehicleHistorySummaryResponse
    dealer: DealerResponse

class VehicleSummaryResponse(BaseModel):
    id: int
    vin: str
    stock_number: str
    make: str
    model: str
    trim: str
    model_year: int
    body_style: str
    drivetrain: str
    mileage: int
    price: float
    third_row_seating: int
    dealer_name: str
    dealer_address: str
    dealer_city: str
    dealer_state: str
    dealer_postal_code: str
    dealer_phone: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/vehicles/used/suv", response_model=List[VehicleSummaryResponse], operation_id="find_used_suv_vehicles")
def find_used_suv_vehicles(
    min_year: Optional[int] = Query(2019, description="Minimum model year (default: 2019)"),
    max_year: Optional[int] = Query(2023, description="Maximum model year (default: 2023)"),
    min_price: Optional[float] = Query(25000, description="Minimum price (default: 25000)"),
    max_price: Optional[float] = Query(40000, description="Maximum price (default: 40000)"),
    max_mileage: Optional[int] = Query(70000, description="Maximum mileage (default: 70000)"),
    sort_by: Optional[Literal["mileage", "price"]] = Query("mileage", description="Sort field (default: mileage)"),
    sort_order: Optional[Literal["asc", "desc"]] = Query("asc", description="Sort order (default: asc)"),
    db = Depends(get_db)
):
    # Build query
    query = db.query(Vehicle).join(Dealership).filter(
        Vehicle.body_style == "SUV",
        Vehicle.model_year >= min_year,
        Vehicle.model_year <= max_year,
        Vehicle.price >= min_price,
        Vehicle.price <= max_price,
        Vehicle.mileage <= max_mileage,
        Vehicle.third_row_seating == 1,
        Vehicle.drivetrain == "AWD",
        Vehicle.status == "available"
    )
    
    # Add feature filters
    required_features = ["Blind Spot Monitoring", "Apple CarPlay", "Backup Camera"]
    for feature in required_features:
        subquery = db.query(VehicleFeature.vehicle_id).filter(VehicleFeature.feature_name == feature).subquery()
        query = query.join(subquery, Vehicle.id == subquery.c.vehicle_id)
    
    # Apply sorting
    if sort_by == "price":
        if sort_order == "asc":
            query = query.order_by(Vehicle.price.asc())
        else:
            query = query.order_by(Vehicle.price.desc())
    else:  # sort_by == "mileage"
        if sort_order == "asc":
            query = query.order_by(Vehicle.mileage.asc())
        else:
            query = query.order_by(Vehicle.mileage.desc())
    
    # Execute query
    vehicles = query.limit(2).all()
    
    # Format response
    result = []
    for vehicle in vehicles:
        result.append({
            "id": vehicle.id,
            "vin": vehicle.vin,
            "stock_number": vehicle.stock_number,
            "make": vehicle.make,
            "model": vehicle.model,
            "trim": vehicle.trim,
            "model_year": vehicle.model_year,
            "body_style": vehicle.body_style,
            "drivetrain": vehicle.drivetrain,
            "mileage": vehicle.mileage,
            "price": vehicle.price,
            "third_row_seating": vehicle.third_row_seating,
            "dealer_name": vehicle.dealership.name,
            "dealer_address": vehicle.dealership.address_line1,
            "dealer_city": vehicle.dealership.city,
            "dealer_state": vehicle.dealership.state,
            "dealer_postal_code": vehicle.dealership.postal_code,
            "dealer_phone": vehicle.dealership.phone
        })
    
    return result

@app.get("/vehicles/{vehicle_id}/details", response_model=VehicleDetailsResponse, operation_id="get_vehicle_details")
def get_vehicle_details(vehicle_id: int, db = Depends(get_db)):
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Get features
    features = [f.feature_name for f in vehicle.features]
    
    # Get photos
    photos = [
        {
            "id": p.id,
            "photo_url": p.photo_url,
            "caption": p.caption,
            "is_primary": p.is_primary
        }
        for p in vehicle.photos
    ]
    
    # Get specifications
    specifications = [
        {
            "spec_key": s.spec_key,
            "spec_value": s.spec_value
        }
        for s in vehicle.specifications
    ]
    
    # Get history summary
    history_summary = vehicle.history_summary
    
    # Get dealer info
    dealer = vehicle.dealership
    
    return {
        "id": vehicle.id,
        "vin": vehicle.vin,
        "stock_number": vehicle.stock_number,
        "make": vehicle.make,
        "model": vehicle.model,
        "trim": vehicle.trim,
        "model_year": vehicle.model_year,
        "body_style": vehicle.body_style,
        "drivetrain": vehicle.drivetrain,
        "transmission": vehicle.transmission,
        "fuel_type": vehicle.fuel_type,
        "exterior_color": vehicle.exterior_color,
        "interior_color": vehicle.interior_color,
        "mileage": vehicle.mileage,
        "price": vehicle.price,
        "third_row_seating": vehicle.third_row_seating,
        "status": vehicle.status,
        "photos": photos,
        "specifications": specifications,
        "features": features,
        "history_summary": {
            "provider": history_summary.provider,
            "report_url": history_summary.report_url,
            "accident_count": history_summary.accident_count,
            "owner_count": history_summary.owner_count,
            "service_record_count": history_summary.service_record_count,
            "has_damage_history": history_summary.has_damage_history,
            "has_frame_damage": history_summary.has_frame_damage,
            "is_clean_title": history_summary.is_clean_title,
            "summary_text": history_summary.summary_text,
            "last_reported_date": history_summary.last_reported_date
        },
        "dealer": {
            "id": dealer.id,
            "name": dealer.name,
            "address_line1": dealer.address_line1,
            "address_line2": dealer.address_line2,
            "city": dealer.city,
            "state": dealer.state,
            "postal_code": dealer.postal_code,
            "phone": dealer.phone,
            "latitude": dealer.latitude,
            "longitude": dealer.longitude
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
