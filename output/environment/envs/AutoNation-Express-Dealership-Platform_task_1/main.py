import os
import sqlite3
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Dealership(Base):
    __tablename__ = "dealerships"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    zip = Column(String, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    stock_number = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    trim = Column(String)
    body_style = Column(String)
    fuel_type = Column(String, nullable=False)
    exterior_color = Column(String, nullable=False)
    interior_color = Column(String)
    price = Column(Float, nullable=False)
    mileage = Column(Integer, default=0)
    transmission = Column(String)
    drivetrain = Column(String)
    engine = Column(String)
    dealership_id = Column(Integer, ForeignKey("dealerships.id"), nullable=False)
    is_new = Column(Integer, default=1)
    status = Column(String, default="available")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Response model
class InventoryItem(BaseModel):
    vin: str
    stock_number: str
    exterior_color: str
    price: float

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/inventory/search", response_model=List[InventoryItem], operation_id="search_new_inventory")
def search_new_inventory(
    make: str = Query(..., description="Vehicle manufacturer"),
    model: str = Query(..., description="Vehicle model"),
    year: int = Query(..., description="Vehicle year"),
    fuel_type: str = Query(..., description="Fuel type of vehicle"),
    max_price: float = Query(..., description="Maximum price threshold"),
    city: str = Query(..., description="City for location-based filtering"),
    state: str = Query(..., description="State for location-based filtering"),
    radius_miles: int = Query(..., description="Search radius in miles"),
    sort_by: Optional[str] = Query(None, description="Sorting criteria (default: price_asc)")
):
    db = next(get_db())
    
    # Get dealer coordinates
    dealer = db.query(Dealership).filter(
        Dealership.city == city,
        Dealership.state == state
    ).first()
    
    if not dealer:
        raise HTTPException(status_code=404, detail="Dealer location not found")
    
    # Calculate bounding box
    lat_range = radius_miles / 69.0
    lon_range = radius_miles / (69.0 * abs(dealer.latitude))
    
    min_lat = dealer.latitude - lat_range
    max_lat = dealer.latitude + lat_range
    min_lon = dealer.longitude - lon_range
    max_lon = dealer.longitude + lon_range
    
    # Build query
    query = db.query(Vehicle).join(Dealership).filter(
        Vehicle.make == make,
        Vehicle.model == model,
        Vehicle.year == year,
        Vehicle.fuel_type == fuel_type,
        Vehicle.price <= max_price,
        Vehicle.is_new == 1,
        Vehicle.status == "available",
        Dealership.latitude >= min_lat,
        Dealership.latitude <= max_lat,
        Dealership.longitude >= min_lon,
        Dealership.longitude <= max_lon
    )
    
    # Apply sorting
    if sort_by == "price_asc":
        query = query.order_by(Vehicle.price.asc())
    else:
        query = query.order_by(Vehicle.price.asc())
    
    # Limit results
    vehicles = query.limit(3).all()
    
    # Format response
    result = []
    for vehicle in vehicles:
        result.append({
            "vin": vehicle.vin,
            "stock_number": vehicle.stock_number,
            "exterior_color": vehicle.exterior_color,
            "price": vehicle.price
        })
    
    return result
