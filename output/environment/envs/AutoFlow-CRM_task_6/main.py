from fastapi import FastAPI, HTTPException, Path, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime

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
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    price = Column(Float)
    status = Column(String, nullable=False, default="available")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("status IN ('available', 'sold', 'reserved', 'maintenance')"),
    )

class Sale(Base):
    __tablename__ = "sales"
    
    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    sale_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    sale_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class VehicleStatusUpdate(BaseModel):
    status: str

class VehicleResponse(BaseModel):
    message: str
    vehicle_id: int
    status: str

class VehicleRemovalResponse(BaseModel):
    message: str
    vehicle_id: int

# Create app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/vehicles/{vin}/status", response_model=VehicleResponse, operation_id="update_vehicle_status")
def update_vehicle_status(
    vin: str,
    status_update: VehicleStatusUpdate,
    db = Depends(get_db)
):
    vehicle = db.query(Vehicle).filter(Vehicle.vin == vin).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Validate status
    valid_statuses = ["available", "sold", "reserved", "maintenance"]
    if status_update.status not in valid_statuses:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    vehicle.status = status_update.status
    vehicle.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(vehicle)
    
    return {
        "message": f"Vehicle status updated to {status_update.status}",
        "vehicle_id": vehicle.id,
        "status": vehicle.status
    }

@app.delete("/vehicles/{vin}", response_model=VehicleRemovalResponse, operation_id="remove_sold_vehicle")
def remove_sold_vehicle(
    vin: str = Path(..., description="The VIN of the vehicle to remove"),
    db = Depends(get_db)
):
    vehicle = db.query(Vehicle).filter(Vehicle.vin == vin).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    if vehicle.status != "sold":
        raise HTTPException(status_code=400, detail="Vehicle must be marked as 'sold' to be removed")
    
    vehicle_id = vehicle.id
    db.delete(vehicle)
    db.commit()
    
    return {
        "message": "Vehicle successfully removed from inventory",
        "vehicle_id": vehicle_id
    }
