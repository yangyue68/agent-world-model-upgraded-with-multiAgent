import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Aircraft(Base):
    __tablename__ = "aircraft"
    id = Column(Integer, primary_key=True, index=True)
    registration = Column(String, unique=True, nullable=False)
    model = Column(String)
    manufacturer = Column(String)
    empty_weight = Column(Float)
    max_takeoff_weight = Column(Float)
    max_fuel_capacity = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Airport(Base):
    __tablename__ = "airports"
    id = Column(Integer, primary_key=True, index=True)
    icao_code = Column(String, unique=True, nullable=False)
    iata_code = Column(String, unique=True)
    name = Column(String, nullable=False)
    city = Column(String)
    country = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    elevation = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Flight(Base):
    __tablename__ = "flights"
    id = Column(Integer, primary_key=True, index=True)
    flight_number = Column(String, nullable=False)
    origin_airport_id = Column(Integer, ForeignKey("airports.id"))
    destination_airport_id = Column(Integer, ForeignKey("airports.id"))
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"))
    scheduled_departure = Column(DateTime)
    scheduled_arrival = Column(DateTime)
    actual_departure = Column(DateTime)
    actual_arrival = Column(DateTime)
    status = Column(String, default="scheduled")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    origin_airport = relationship("Airport", foreign_keys=[origin_airport_id])
    destination_airport = relationship("Airport", foreign_keys=[destination_airport_id])
    aircraft = relationship("Aircraft")

class FlightLeg(Base):
    __tablename__ = "flight_legs"
    id = Column(Integer, primary_key=True, index=True)
    flight_id = Column(Integer, ForeignKey("flights.id"))
    leg_number = Column(Integer, nullable=False)
    origin_airport_id = Column(Integer, ForeignKey("airports.id"))
    destination_airport_id = Column(Integer, ForeignKey("airports.id"))
    departure_time = Column(DateTime)
    arrival_time = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    flight = relationship("Flight")
    origin_airport = relationship("Airport", foreign_keys=[origin_airport_id])
    destination_airport = relationship("Airport", foreign_keys=[destination_airport_id])

class FuelUplift(Base):
    __tablename__ = "fuel_uplifts"
    id = Column(Integer, primary_key=True, index=True)
    flight_leg_id = Column(Integer, ForeignKey("flight_legs.id"))
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"))
    fuel_amount_lbs = Column(Float, nullable=False)
    uplift_time = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    flight_leg = relationship("FlightLeg")
    aircraft = relationship("Aircraft")
    user = relationship("User")

class WeightBalance(Base):
    __tablename__ = "weight_balance"
    id = Column(Integer, primary_key=True, index=True)
    flight_leg_id = Column(Integer, ForeignKey("flight_legs.id"), unique=True)
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"))
    passenger_count = Column(Integer, nullable=False)
    baggage_weight_lbs = Column(Float, nullable=False)
    catering_weight_lbs = Column(Float, nullable=False)
    fuel_weight_lbs = Column(Float, nullable=False)
    empty_weight_lbs = Column(Float, nullable=False)
    total_weight_lbs = Column(Float, nullable=False)
    calculated_by = Column(Integer, ForeignKey("users.id"))
    calculated_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    flight_leg = relationship("FlightLeg")
    aircraft = relationship("Aircraft")
    user = relationship("User")

# Pydantic Models
class FlightSearchRequest(BaseModel):
    flight_number: str
    leg_number: int

class FlightSearchResponse(BaseModel):
    flight_id: int
    flight_leg_id: int

class AircraftSearchRequest(BaseModel):
    registration: str

class AircraftSearchResponse(BaseModel):
    id: int

class FuelUpliftRequest(BaseModel):
    flight_leg_id: int
    aircraft_id: int
    fuel_amount_lbs: float
    created_by: int

class FuelUpliftResponse(BaseModel):
    id: int
    flight_leg_id: int
    aircraft_id: int
    fuel_amount_lbs: float
    uplift_time: datetime
    created_by: int

class WeightBalanceRequest(BaseModel):
    flight_leg_id: int
    aircraft_id: int
    passenger_count: int
    baggage_weight_lbs: float
    catering_weight_lbs: float
    calculated_by: int

class WeightBalanceResponse(BaseModel):
    id: int
    flight_leg_id: int
    aircraft_id: int
    passenger_count: int
    baggage_weight_lbs: float
    catering_weight_lbs: float
    fuel_weight_lbs: float
    empty_weight_lbs: float
    total_weight_lbs: float
    calculated_by: int
    calculated_at: datetime

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/flights/search", response_model=FlightSearchResponse, operation_id="search_flights")
def search_flights(flight_number: str, leg_number: int, db=None):
    if db is None:
        db = next(get_db())
    
    flight = db.query(Flight).filter(Flight.flight_number == flight_number).first()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    
    flight_leg = db.query(FlightLeg).filter(
        FlightLeg.flight_id == flight.id,
        FlightLeg.leg_number == leg_number
    ).first()
    
    if not flight_leg:
        raise HTTPException(status_code=404, detail="Flight leg not found")
    
    return FlightSearchResponse(
        flight_id=flight.id,
        flight_leg_id=flight_leg.id
    )

@app.get("/aircrafts/search", response_model=AircraftSearchResponse, operation_id="search_aircrafts")
def search_aircrafts(registration: str, db=None):
    if db is None:
        db = next(get_db())
    
    aircraft = db.query(Aircraft).filter(Aircraft.registration == registration).first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    return AircraftSearchResponse(id=aircraft.id)

@app.post("/fuel-uplifts", response_model=FuelUpliftResponse, operation_id="log_fuel_uplift")
def log_fuel_uplift(request: FuelUpliftRequest, db=None):
    if db is None:
        db = next(get_db())
    
    # Validate that the aircraft exists
    aircraft = db.query(Aircraft).filter(Aircraft.id == request.aircraft_id).first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Validate that the flight leg exists
    flight_leg = db.query(FlightLeg).filter(FlightLeg.id == request.flight_leg_id).first()
    if not flight_leg:
        raise HTTPException(status_code=404, detail="Flight leg not found")
    
    # Validate that the user exists
    user = db.query(User).filter(User.id == request.created_by).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Create fuel uplift record
    fuel_uplift = FuelUplift(
        flight_leg_id=request.flight_leg_id,
        aircraft_id=request.aircraft_id,
        fuel_amount_lbs=request.fuel_amount_lbs,
        created_by=request.created_by
    )
    
    db.add(fuel_uplift)
    db.commit()
    db.refresh(fuel_uplift)
    
    return FuelUpliftResponse(
        id=fuel_uplift.id,
        flight_leg_id=fuel_uplift.flight_leg_id,
        aircraft_id=fuel_uplift.aircraft_id,
        fuel_amount_lbs=fuel_uplift.fuel_amount_lbs,
        uplift_time=fuel_uplift.uplift_time,
        created_by=fuel_uplift.created_by
    )

@app.post("/weight-balance-calculations", response_model=WeightBalanceResponse, operation_id="calculate_and_save_weight_balance")
def calculate_and_save_weight_balance(request: WeightBalanceRequest, db=None):
    if db is None:
        db = next(get_db())
    
    # Validate that the aircraft exists
    aircraft = db.query(Aircraft).filter(Aircraft.id == request.aircraft_id).first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Validate that the flight leg exists
    flight_leg = db.query(FlightLeg).filter(FlightLeg.id == request.flight_leg_id).first()
    if not flight_leg:
        raise HTTPException(status_code=404, detail="Flight leg not found")
    
    # Validate that the user exists
    user = db.query(User).filter(User.id == request.calculated_by).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get the latest fuel uplift for this flight leg to determine fuel weight
    latest_fuel_uplift = db.query(FuelUplift).filter(
        FuelUplift.flight_leg_id == request.flight_leg_id
    ).order_by(FuelUplift.uplift_time.desc()).first()
    
    fuel_weight = latest_fuel_uplift.fuel_amount_lbs if latest_fuel_uplift else 0.0
    
    # Calculate total weight
    total_weight = (
        aircraft.empty_weight +
        fuel_weight +
        (request.passenger_count * 180) +  # Assuming 180 lbs per passenger
        request.baggage_weight_lbs +
        request.catering_weight_lbs
    )
    
    # Create weight balance record
    weight_balance = WeightBalance(
        flight_leg_id=request.flight_leg_id,
        aircraft_id=request.aircraft_id,
        passenger_count=request.passenger_count,
        baggage_weight_lbs=request.baggage_weight_lbs,
        catering_weight_lbs=request.catering_weight_lbs,
        fuel_weight_lbs=fuel_weight,
        empty_weight_lbs=aircraft.empty_weight,
        total_weight_lbs=total_weight,
        calculated_by=request.calculated_by
    )
    
    db.add(weight_balance)
    db.commit()
    db.refresh(weight_balance)
    
    return WeightBalanceResponse(
        id=weight_balance.id,
        flight_leg_id=weight_balance.flight_leg_id,
        aircraft_id=weight_balance.aircraft_id,
        passenger_count=weight_balance.passenger_count,
        baggage_weight_lbs=weight_balance.baggage_weight_lbs,
        catering_weight_lbs=weight_balance.catering_weight_lbs,
        fuel_weight_lbs=weight_balance.fuel_weight_lbs,
        empty_weight_lbs=weight_balance.empty_weight_lbs,
        total_weight_lbs=weight_balance.total_weight_lbs,
        calculated_by=weight_balance.calculated_by,
        calculated_at=weight_balance.calculated_at
    )
