import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Query, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, REAL, ForeignKey, func, case, and_, or_
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
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Pilot(Base):
    __tablename__ = "pilots"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    license_number = Column(String, unique=True, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    date_of_birth = Column(Date)
    hire_date = Column(Date)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User")

class Aircraft(Base):
    __tablename__ = "aircraft"
    id = Column(Integer, primary_key=True, index=True)
    registration = Column(String, unique=True, nullable=False)
    model = Column(String, nullable=False)
    manufacturer = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Airport(Base):
    __tablename__ = "airports"
    id = Column(Integer, primary_key=True, index=True)
    iata_code = Column(String, unique=True, nullable=False)
    icao_code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    city = Column(String)
    country = Column(String)
    timezone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PilotCertification(Base):
    __tablename__ = "pilot_certifications"
    id = Column(Integer, primary_key=True, index=True)
    pilot_id = Column(Integer, ForeignKey("pilots.id"))
    aircraft_model = Column(String, nullable=False)
    certification_date = Column(Date, nullable=False)
    expiry_date = Column(Date)
    is_valid = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class FlightAssignment(Base):
    __tablename__ = "flight_assignments"
    id = Column(Integer, primary_key=True, index=True)
    pilot_id = Column(Integer, ForeignKey("pilots.id"))
    flight_number = Column(String, nullable=False)
    departure_airport_id = Column(Integer, ForeignKey("airports.id"))
    arrival_airport_id = Column(Integer, ForeignKey("airports.id"))
    scheduled_departure = Column(DateTime, nullable=False)
    scheduled_arrival = Column(DateTime, nullable=False)
    actual_departure = Column(DateTime)
    actual_arrival = Column(DateTime)
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"))
    status = Column(String, default="scheduled")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PilotRestPeriod(Base):
    __tablename__ = "pilot_rest_periods"
    id = Column(Integer, primary_key=True, index=True)
    pilot_id = Column(Integer, ForeignKey("pilots.id"))
    rest_start = Column(DateTime, nullable=False)
    rest_end = Column(DateTime, nullable=False)
    is_mandatory = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PilotFlightTime(Base):
    __tablename__ = "pilot_flight_time"
    id = Column(Integer, primary_key=True, index=True)
    pilot_id = Column(Integer, ForeignKey("pilots.id"))
    aircraft_model = Column(String, nullable=False)
    flight_date = Column(Date, nullable=False)
    hours = Column(REAL, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class PilotInfo(BaseModel):
    pilot_id: int
    full_name: str

class PilotWithFlightTime(PilotInfo):
    recent_g650_flight_time: float

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/pilots/g650-certified-with-rest", response_model=List[PilotInfo], operation_id="find_g650_certified_pilots_with_rest")
def find_g650_certified_pilots_with_rest(
    rest_window_hours: int = Query(48, ge=0),
    db = Depends(get_db)
):
    current_time = datetime.utcnow()
    end_time = current_time + timedelta(hours=rest_window_hours)
    
    # Find pilots certified on Gulfstream G650 with upcoming rest periods
    query = db.query(Pilot.id.label('pilot_id'), 
                     func.concat(Pilot.first_name, ' ', Pilot.last_name).label('full_name')) \
             .join(PilotCertification, Pilot.id == PilotCertification.pilot_id) \
             .join(PilotRestPeriod, Pilot.id == PilotRestPeriod.pilot_id) \
             .filter(PilotCertification.aircraft_model == 'Gulfstream G650') \
             .filter(PilotCertification.is_valid == 1) \
             .filter(PilotRestPeriod.rest_start >= current_time) \
             .filter(PilotRestPeriod.rest_end <= end_time) \
             .distinct()
    
    results = query.all()
    return [{"pilot_id": row.pilot_id, "full_name": row.full_name} for row in results]

@app.get("/pilots/g650-certified-available-for-charter", response_model=List[PilotWithFlightTime], operation_id="find_g650_certified_available_for_charter")
def find_g650_certified_available_for_charter(
    charter_departure_utc: str,
    charter_arrival_utc: str,
    db = Depends(get_db)
):
    departure_time = datetime.fromisoformat(charter_departure_utc.replace('Z', '+00:00'))
    arrival_time = datetime.fromisoformat(charter_arrival_utc.replace('Z', '+00:00'))
    
    # Find pilots certified on Gulfstream G650 who are available during charter window
    query = db.query(Pilot.id.label('pilot_id'),
                     func.concat(Pilot.first_name, ' ', Pilot.last_name).label('full_name'),
                     func.sum(PilotFlightTime.hours).label('recent_g650_flight_time')) \
             .join(PilotCertification, Pilot.id == PilotCertification.pilot_id) \
             .outerjoin(FlightAssignment, and_(FlightAssignment.pilot_id == Pilot.id,
                                               FlightAssignment.scheduled_departure < arrival_time,
                                               FlightAssignment.scheduled_arrival > departure_time)) \
             .outerjoin(PilotFlightTime, and_(PilotFlightTime.pilot_id == Pilot.id,
                                              PilotFlightTime.aircraft_model == 'Gulfstream G650',
                                              PilotFlightTime.flight_date >= (datetime.utcnow() - timedelta(days=90)))) \
             .filter(PilotCertification.aircraft_model == 'Gulfstream G650') \
             .filter(PilotCertification.is_valid == 1) \
             .filter(FlightAssignment.id.is_(None)) \
             .group_by(Pilot.id, Pilot.first_name, Pilot.last_name) \
             .having(func.sum(PilotFlightTime.hours).isnot(None))
    
    results = query.all()
    return [
        {
            "pilot_id": row.pilot_id,
            "full_name": row.full_name,
            "recent_g650_flight_time": float(row.recent_g650_flight_time or 0)
        } for row in results
    ]

@app.get("/pilots/{pilot_id}/g650-flight-time-last-90-days", response_model=PilotWithFlightTime, operation_id="get_pilot_g650_flight_time_last_90_days")
def get_pilot_g650_flight_time_last_90_days(
    pilot_id: int,
    db = Depends(get_db)
):
    start_date = datetime.utcnow() - timedelta(days=90)
    
    total_flight_time = db.query(func.sum(PilotFlightTime.hours)) \
                          .filter(PilotFlightTime.pilot_id == pilot_id) \
                          .filter(PilotFlightTime.aircraft_model == 'Gulfstream G650') \
                          .filter(PilotFlightTime.flight_date >= start_date) \
                          .scalar() or 0
    
    pilot = db.query(Pilot).filter(Pilot.id == pilot_id).first()
    if not pilot:
        raise HTTPException(status_code=404, detail="Pilot not found")
    
    return {
        "pilot_id": pilot_id,
        "full_name": f"{pilot.first_name} {pilot.last_name}",
        "recent_g650_flight_time": float(total_flight_time)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
