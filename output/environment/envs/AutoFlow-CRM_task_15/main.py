from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime

DATABASE_PATH = os.environ.get("DATABASE_PATH")

app = FastAPI()

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = 'vehicles'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False, info={'check': 'year >= 1900 AND year <= 2100'})
    mileage = Column(Integer, nullable=False, info={'check': 'mileage >= 0'})
    condition = Column(String, nullable=False, info={'check': "condition IN ('New', 'Used', 'Certified Pre-Owned')"})
    price = Column(Float, nullable=False, info={'check': 'price >= 0'})
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)

class VehicleCreate(BaseModel):
    vin: str
    make: str
    model: str
    year: int
    mileage: int
    condition: str
    price: float

class VehicleResponse(BaseModel):
    id: int
    vin: str
    make: str
    model: str
    year: int
    mileage: int
    condition: str
    price: float
    created_by: int

@app.post("/vehicles", response_model=VehicleResponse, operation_id="add_vehicle_to_inventory")
def add_vehicle_to_inventory(vehicle: VehicleCreate):
    engine = create_engine(DATABASE_PATH)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Get current user from environment
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
        
        # Create new vehicle
        new_vehicle = Vehicle(
            vin=vehicle.vin,
            make=vehicle.make,
            model=vehicle.model,
            year=vehicle.year,
            mileage=vehicle.mileage,
            condition=vehicle.condition,
            price=vehicle.price,
            created_by=current_user_id
        )
        
        session.add(new_vehicle)
        session.commit()
        session.refresh(new_vehicle)
        
        return VehicleResponse(
            id=new_vehicle.id,
            vin=new_vehicle.vin,
            make=new_vehicle.make,
            model=new_vehicle.model,
            year=new_vehicle.year,
            mileage=new_vehicle.mileage,
            condition=new_vehicle.condition,
            price=new_vehicle.price,
            created_by=new_vehicle.created_by
        )
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()
