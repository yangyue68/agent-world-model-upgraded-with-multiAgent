import os
from typing import Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Index, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define models based on the schema
class Dealership(Base):
    __tablename__ = "dealerships"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    zip_code = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))

class Vehicle(Base):
    __tablename__ = "vehicles"
    
    id = Column(Integer, primary_key=True, index=True)
    dealership_id = Column(Integer, ForeignKey("dealerships.id"), nullable=False)
    vin = Column(String, nullable=False, unique=True)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    body_type = Column(String, nullable=False)
    mileage = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    is_certified_pre_owned = Column(Boolean, nullable=False, default=False)
    exterior_color = Column(String)
    interior_color = Column(String)
    transmission = Column(String)
    fuel_type = Column(String)
    status = Column(String, nullable=False, default="available")
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    
    # Relationship
    dealership = relationship("Dealership", back_populates="vehicles")

# Create relationships
Dealership.vehicles = relationship("Vehicle", order_by=Vehicle.id, back_populates="dealership")

app = FastAPI()

class VehicleResponse(BaseModel):
    id: int
    vin: str
    make: str
    model: str
    year: int
    mileage: int
    price: float
    exterior_color: str
    interior_color: str
    transmission: str
    fuel_type: str
    dealership_name: str

@app.get("/search-certified-suv-vehicles", response_model=list[VehicleResponse], operation_id="search_certified_suv_vehicles")
def search_certified_suv_vehicles(
    dealership_name: str = Query(..., description="Name of the dealership to filter vehicles"),
    max_mileage: Optional[int] = Query(30000, description="Maximum mileage threshold (default: 30000)"),
    min_price: Optional[float] = Query(25000, description="Minimum price threshold (default: 25000)"),
    max_price: Optional[float] = Query(40000, description="Maximum price threshold (default: 40000)")
):
    db = SessionLocal()
    try:
        # Build the query
        query = db.query(Vehicle, Dealership.name.label("dealership_name")).join(Dealership)
        
        # Apply filters
        query = query.filter(
            Dealership.name == dealership_name,
            Vehicle.body_type == "SUV",
            Vehicle.is_certified_pre_owned == True,
            Vehicle.mileage <= max_mileage,
            Vehicle.price >= min_price,
            Vehicle.price <= max_price
        )
        
        # Order by price ascending
        query = query.order_by(Vehicle.price.asc())
        
        # Execute query
        results = query.all()
        
        # Format response
        vehicles = []
        for vehicle, dealership_name in results:
            vehicles.append({
                "id": vehicle.id,
                "vin": vehicle.vin,
                "make": vehicle.make,
                "model": vehicle.model,
                "year": vehicle.year,
                "mileage": vehicle.mileage,
                "price": vehicle.price,
                "exterior_color": vehicle.exterior_color,
                "interior_color": vehicle.interior_color,
                "transmission": vehicle.transmission,
                "fuel_type": vehicle.fuel_type,
                "dealership_name": dealership_name
            })
        
        return vehicles
    finally:
        db.close()
