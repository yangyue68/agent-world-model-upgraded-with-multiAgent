import os
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, func, and_, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True, index=True)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    vin = Column(String, unique=True)
    trim = Column(String)
    body_style = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    source = Column(String, nullable=False)
    status = Column(String, default="new")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class VehicleResponse(BaseModel):
    id: int

class LeadResponse(BaseModel):
    id: int
    name: str
    phone: str
    source: str
    status: str
    created_at: datetime

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/vehicles/search", response_model=VehicleResponse, operation_id="search_vehicles")
def search_vehicles(
    make: str,
    model: str,
    year: int,
    db=None
):
    if db is None:
        db = next(get_db())
    
    vehicle = db.query(Vehicle).filter(
        and_(
            Vehicle.make == make,
            Vehicle.model == model,
            Vehicle.year == year
        )
    ).first()
    
    if vehicle:
        return {"id": vehicle.id}
    else:
        return {"id": None}

@app.get("/leads", response_model=List[LeadResponse], operation_id="find_recent_leads_for_vehicle")
def find_recent_leads_for_vehicle(
    vehicle_id: int,
    db=None
):
    if db is None:
        db = next(get_db())
    
    # Calculate the date 14 days ago
    fourteen_days_ago = datetime.utcnow() - timedelta(days=14)
    
    # Get leads created in the last 14 days with status 'new' or 'contacted'
    # and associated with the specified vehicle
    leads = db.query(Lead).filter(
        and_(
            Lead.vehicle_id == vehicle_id,
            Lead.created_at >= fourteen_days_ago,
            Lead.status.in_(["new", "contacted"])
        )
    ).all()
    
    # Convert to response format
    results = []
    for lead in leads:
        results.append({
            "id": lead.id,
            "name": lead.name,
            "phone": lead.phone,
            "source": lead.source,
            "status": lead.status,
            "created_at": lead.created_at
        })
    
    return results
