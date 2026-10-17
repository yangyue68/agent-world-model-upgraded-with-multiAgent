import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Path, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Text, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

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
    stock_number = Column(String, unique=True, nullable=False)
    vin = Column(String, unique=True)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    trim = Column(String)
    color = Column(String)
    mileage = Column(Integer)
    price = Column(Float)
    status = Column(String, nullable=False, default="active")
    sold_at = Column(DateTime)
    auctioned_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("status IN ('active', 'sold', 'pending', 'auctioned', 'removed')"),
    )

class VehiclePhoto(Base):
    __tablename__ = "vehicle_photos"
    
    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    photo_url = Column(String, nullable=False)
    caption = Column(String)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class InventoryAction(Base):
    __tablename__ = "inventory_actions"
    
    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    action_type = Column(String, nullable=False)
    action_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    performed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("action_type IN ('listed', 'sold', 'auctioned', 'removed', 'updated')"),
    )

# Pydantic Models
class VehicleDetailsResponse(BaseModel):
    id: int
    stock_number: str
    vin: str
    make: str
    model: str
    year: int
    trim: Optional[str]
    color: str
    mileage: int
    price: float
    status: str
    sold_at: Optional[datetime]
    auctioned_at: datetime
    created_at: datetime
    updated_at: datetime

class SearchVehiclesResponse(BaseModel):
    id: int
    stock_number: str
    vin: str
    make: str
    model: str
    year: int
    trim: Optional[str]
    color: str
    mileage: int
    price: float
    status: str
    sold_at: Optional[datetime]
    auctioned_at: datetime
    created_at: datetime
    updated_at: datetime

class RemoveVehicleListingResponse(BaseModel):
    message: str
    vehicle_id: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.delete("/vehicles/{stock_number}", response_model=RemoveVehicleListingResponse, operation_id="remove_vehicle_listing")
def remove_vehicle_listing(
    stock_number: str = Path(..., description="The stock number of the vehicle to remove"),
    db: Session = Depends(get_db)
):
    # Get the vehicle by stock number
    vehicle = db.query(Vehicle).filter(Vehicle.stock_number == stock_number).first()
    
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Verify the vehicle was auctioned on 2024-10-15
    if not vehicle.auctioned_at or vehicle.auctioned_at.date() != datetime(2024, 10, 15).date():
        raise HTTPException(status_code=400, detail="Vehicle was not auctioned on 2024-10-15")
    
    # Record the removal action
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    inventory_action = InventoryAction(
        vehicle_id=vehicle.id,
        action_type="removed",
        performed_by=current_user_id,
        notes=f"Vehicle removed from inventory after auction on {vehicle.auctioned_at.date()}"
    )
    db.add(inventory_action)
    
    # Delete vehicle photos (will cascade delete due to FK constraint)
    db.query(VehiclePhoto).filter(VehiclePhoto.vehicle_id == vehicle.id).delete()
    
    # Update vehicle status to removed
    vehicle.status = "removed"
    db.commit()
    
    return RemoveVehicleListingResponse(
        message="Vehicle listing and associated photos successfully removed",
        vehicle_id=vehicle.id
    )

@app.get("/vehicles/{stock_number}/details", response_model=VehicleDetailsResponse, operation_id="get_vehicle_details")
def get_vehicle_details(
    stock_number: str = Path(..., description="The stock number of the vehicle to retrieve details for"),
    db: Session = Depends(get_db)
):
    vehicle = db.query(Vehicle).filter(Vehicle.stock_number == stock_number).first()
    
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    return vehicle

@app.get("/vehicles/search", response_model=list[SearchVehiclesResponse], operation_id="search_vehicles_by_auction_date")
def search_vehicles_by_auction_date(
    auctioned_at: str = Path(..., description="The date when the vehicle was auctioned (format: YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    # Parse the auctioned_at date
    try:
        auction_date = datetime.strptime(auctioned_at, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD")
    
    vehicles = db.query(Vehicle).filter(Vehicle.auctioned_at >= auction_date).filter(Vehicle.auctioned_at < auction_date.replace(day=auction_date.day + 1)).all()
    
    return vehicles
