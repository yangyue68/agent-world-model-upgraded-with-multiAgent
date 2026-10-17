import os
from datetime import datetime
from typing import Optional, Generator
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, text
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Aircraft(Base):
    __tablename__ = "aircraft"
    id = Column(Integer, primary_key=True)
    tail_number = Column(String, unique=True, nullable=False)
    model = Column(String)
    empty_weight = Column(Float)
    max_takeoff_weight = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Flight(Base):
    __tablename__ = "flights"
    id = Column(Integer, primary_key=True)
    flight_number = Column(String, unique=True, nullable=False)
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"))
    origin = Column(String, nullable=False)
    destination = Column(String, nullable=False)
    departure_time = Column(DateTime)
    arrival_time = Column(DateTime)
    status = Column(String, default="scheduled")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class WeightBalance(Base):
    __tablename__ = "weight_balance"
    id = Column(Integer, primary_key=True)
    flight_id = Column(Integer, ForeignKey("flights.id"), unique=True)
    passenger_count = Column(Integer, nullable=False)
    baggage_weight = Column(Float, nullable=False)
    total_weight = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class FuelLog(Base):
    __tablename__ = "fuel_logs"
    id = Column(Integer, primary_key=True)
    flight_id = Column(Integer, ForeignKey("flights.id"))
    fuel_uplift = Column(Float, nullable=False)
    logged_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

# Request Models
class UpdateFlightDestinationRequest(BaseModel):
    destination: str
    passenger_count: int
    baggage_weight: float

class LogFuelUpliftRequest(BaseModel):
    flight_id: int
    fuel_uplift: float
    logged_by: int

# Response Models
class FlightResponse(BaseModel):
    flight_id: int
    destination: str
    passenger_count: int
    baggage_weight: float
    total_weight: float

class FuelLogResponse(BaseModel):
    id: int
    flight_id: int
    fuel_uplift: float
    logged_by: int

# FastAPI App
app = FastAPI()

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/flights/{flight_id}", response_model=FlightResponse, operation_id="update_flight_destination")
def update_flight_destination(
    flight_id: int,
    payload: UpdateFlightDestinationRequest,
    db: Session = Depends(get_db)
):
    # Get the flight
    flight = db.query(Flight).filter(Flight.id == flight_id).first()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    # Update destination
    flight.destination = payload.destination
    
    # Get aircraft info for weight calculations
    aircraft = db.query(Aircraft).filter(Aircraft.id == flight.aircraft_id).first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")

    # Calculate new total weight
    # Passenger weight assumed to be 180 lbs per passenger
    passenger_weight = payload.passenger_count * 180.0
    total_weight = aircraft.empty_weight + passenger_weight + payload.baggage_weight
    
    # Update weight_balance
    weight_record = db.query(WeightBalance).filter(WeightBalance.flight_id == flight_id).first()
    if not weight_record:
        raise HTTPException(status_code=404, detail="Weight balance record not found")
    
    weight_record.passenger_count = payload.passenger_count
    weight_record.baggage_weight = payload.baggage_weight
    weight_record.total_weight = total_weight
    
    # Commit changes
    db.commit()
    db.refresh(flight)
    db.refresh(weight_record)
    
    return FlightResponse(
        flight_id=flight.id,
        destination=flight.destination,
        passenger_count=weight_record.passenger_count,
        baggage_weight=weight_record.baggage_weight,
        total_weight=weight_record.total_weight
    )

@app.post("/fuel_logs", response_model=FuelLogResponse, operation_id="log_fuel_uplift")
def log_fuel_uplift(
    payload: LogFuelUpliftRequest,
    db: Session = Depends(get_db)
):
    # Verify flight exists
    flight = db.query(Flight).filter(Flight.id == payload.flight_id).first()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    
    # Verify user exists
    user = db.query(User).filter(User.id == payload.logged_by).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Create fuel log entry
    fuel_log = FuelLog(
        flight_id=payload.flight_id,
        fuel_uplift=payload.fuel_uplift,
        logged_by=payload.logged_by
    )
    
    db.add(fuel_log)
    db.commit()
    db.refresh(fuel_log)
    
    return FuelLogResponse(
        id=fuel_log.id,
        flight_id=fuel_log.flight_id,
        fuel_uplift=fuel_log.fuel_uplift,
        logged_by=fuel_log.logged_by
    )
