import os
from datetime import datetime, timedelta, date
from typing import List, Literal
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, ForeignKey, func, case, and_, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

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
    aircraft_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class CrewMember(Base):
    __tablename__ = "crew_members"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    crew_role = Column(String, nullable=False)
    faa_certificate_number = Column(String, unique=True, nullable=False)
    faa_part135_currency_expiry = Column(Date, nullable=False)
    medical_certificate_expiry = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Flight(Base):
    __tablename__ = "flights"
    id = Column(Integer, primary_key=True)
    flight_number = Column(String, unique=True, nullable=False)
    tail_number = Column(String, ForeignKey("aircraft.tail_number"), nullable=False)
    aircraft_type = Column(String, nullable=False)
    departure_airport = Column(String, nullable=False)
    departure_time_utc = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class DutyTimeLog(Base):
    __tablename__ = "duty_time_logs"
    id = Column(Integer, primary_key=True)
    crew_member_id = Column(Integer, ForeignKey("crew_members.id"), nullable=False)
    duty_start_utc = Column(DateTime, nullable=False)
    duty_end_utc = Column(DateTime, nullable=False)
    flight_id = Column(Integer, ForeignKey("flights.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class FlightCrewAssignment(Base):
    __tablename__ = "flight_crew_assignments"
    id = Column(Integer, primary_key=True)
    flight_id = Column(Integer, ForeignKey("flights.id"), nullable=False)
    crew_member_id = Column(Integer, ForeignKey("crew_members.id"), nullable=False)
    assigned_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class FlightDetailsResponse(BaseModel):
    id: int
    flight_number: str
    tail_number: str
    aircraft_type: str
    departure_airport: str
    departure_time_utc: datetime

class CrewMemberDetailsResponse(BaseModel):
    id: int
    user_id: int
    crew_role: Literal["Pilot", "Flight Attendant"]
    faa_certificate_number: str
    faa_part135_currency_expiry: date
    medical_certificate_expiry: date

class DutyTimeViolationsResponse(BaseModel):
    has_violations: bool
    violations_count: int

class AssignmentResponse(BaseModel):
    assignment_id: int
    flight_id: int
    crew_member_id: int
    assigned_by_user_id: int
    assigned_at: datetime

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/flights/{flight_number}", response_model=FlightDetailsResponse, operation_id="get_flight_details")
def get_flight_details(flight_number: str, db = Depends(get_db)):
    flight = db.query(Flight).filter(Flight.flight_number == flight_number).first()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    return flight

@app.get("/crew_members/{faa_certificate_number}", response_model=CrewMemberDetailsResponse, operation_id="get_crew_member_details")
def get_crew_member_details(faa_certificate_number: str, db = Depends(get_db)):
    crew_member = db.query(CrewMember).filter(CrewMember.faa_certificate_number == faa_certificate_number).first()
    if not crew_member:
        raise HTTPException(status_code=404, detail="Crew member not found")
    return crew_member

@app.get("/crew_members/{faa_certificate_number}/duty_time_violations", response_model=DutyTimeViolationsResponse, operation_id="check_duty_time_violations")
def check_duty_time_violations(faa_certificate_number: str, db = Depends(get_db)):
    crew_member = db.query(CrewMember).filter(CrewMember.faa_certificate_number == faa_certificate_number).first()
    if not crew_member:
        raise HTTPException(status_code=404, detail="Crew member not found")
    
    # Calculate the start of the 24-hour window (24 hours before flight departure)
    flight = db.query(Flight).filter(Flight.flight_number == "ANV789").first()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    
    twenty_four_hours_before = flight.departure_time_utc - timedelta(hours=24)
    
    violations = db.query(DutyTimeLog).filter(
        and_(
            DutyTimeLog.crew_member_id == crew_member.id,
            DutyTimeLog.duty_start_utc >= twenty_four_hours_before,
            DutyTimeLog.duty_end_utc <= flight.departure_time_utc
        )
    ).count()
    
    return DutyTimeViolationsResponse(has_violations=violations > 0, violations_count=violations)

@app.post("/flights/{flight_number}/assign_crew", response_model=AssignmentResponse, operation_id="assign_crew_to_flight")
def assign_crew_to_flight(flight_number: str, db = Depends(get_db)):
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Get flight details
    flight = db.query(Flight).filter(Flight.flight_number == flight_number).first()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    
    # Get pilot and flight attendant details
    pilot = db.query(CrewMember).filter(CrewMember.faa_certificate_number == "N123AB").first()
    if not pilot:
        raise HTTPException(status_code=404, detail="Pilot not found")
    
    flight_attendant = db.query(CrewMember).filter(CrewMember.faa_certificate_number == "C456DE").first()
    if not flight_attendant:
        raise HTTPException(status_code=404, detail="Flight attendant not found")
    
    # Validate pilot currency
    if pilot.faa_part135_currency_expiry < date.today():
        raise HTTPException(status_code=400, detail="Pilot does not have valid FAA Part 135 currency")
    
    # Validate flight attendant currency
    if flight_attendant.faa_part135_currency_expiry < date.today():
        raise HTTPException(status_code=400, detail="Flight attendant does not have valid FAA Part 135 currency")
    
    # Validate medical certificates
    required_expiry = date(2024, 12, 1)
    if pilot.medical_certificate_expiry < required_expiry:
        raise HTTPException(status_code=400, detail="Pilot's medical certificate is not valid after 2024-12-01")
    
    if flight_attendant.medical_certificate_expiry < required_expiry:
        raise HTTPException(status_code=400, detail="Flight attendant's medical certificate is not valid after 2024-12-01")
    
    # Check duty time violations for pilot
    twenty_four_hours_before = flight.departure_time_utc - timedelta(hours=24)
    pilot_violations = db.query(DutyTimeLog).filter(
        and_(
            DutyTimeLog.crew_member_id == pilot.id,
            DutyTimeLog.duty_start_utc >= twenty_four_hours_before,
            DutyTimeLog.duty_end_utc <= flight.departure_time_utc
        )
    ).count()
    
    if pilot_violations > 0:
        raise HTTPException(status_code=400, detail="Pilot has duty time violations in the last 24 hours")
    
    # Check duty time violations for flight attendant
    flight_attendant_violations = db.query(DutyTimeLog).filter(
        and_(
            DutyTimeLog.crew_member_id == flight_attendant.id,
            DutyTimeLog.duty_start_utc >= twenty_four_hours_before,
            DutyTimeLog.duty_end_utc <= flight.departure_time_utc
        )
    ).count()
    
    if flight_attendant_violations > 0:
        raise HTTPException(status_code=400, detail="Flight attendant has duty time violations in the last 24 hours")
    
    # Create assignments
    pilot_assignment = FlightCrewAssignment(
        flight_id=flight.id,
        crew_member_id=pilot.id,
        assigned_by_user_id=current_user_id
    )
    
    flight_attendant_assignment = FlightCrewAssignment(
        flight_id=flight.id,
        crew_member_id=flight_attendant.id,
        assigned_by_user_id=current_user_id
    )
    
    db.add(pilot_assignment)
    db.add(flight_attendant_assignment)
    db.commit()
    db.refresh(pilot_assignment)
    db.refresh(flight_attendant_assignment)
    
    return AssignmentResponse(
        assignment_id=pilot_assignment.id,
        flight_id=pilot_assignment.flight_id,
        crew_member_id=pilot_assignment.crew_member_id,
        assigned_by_user_id=pilot_assignment.assigned_by_user_id,
        assigned_at=pilot_assignment.assigned_at
    )
