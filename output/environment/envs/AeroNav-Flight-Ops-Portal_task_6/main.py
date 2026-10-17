import os
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, ForeignKey, func, case, and_, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

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
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Aircraft(Base):
    __tablename__ = "aircraft"
    id = Column(Integer, primary_key=True, index=True)
    tail_number = Column(String, unique=True, nullable=False)
    model = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CrewMember(Base):
    __tablename__ = "crew_members"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    employee_id = Column(String, unique=True, nullable=False)
    role = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Flight(Base):
    __tablename__ = "flights"
    id = Column(Integer, primary_key=True, index=True)
    flight_number = Column(String, unique=True, nullable=False)
    origin = Column(String, nullable=False)
    destination = Column(String, nullable=False)
    departure_utc = Column(DateTime, nullable=False)
    arrival_utc = Column(DateTime)
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"), nullable=False)
    status = Column(String, default="Scheduled")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class FlightCrewAssignment(Base):
    __tablename__ = "flight_crew_assignments"
    id = Column(Integer, primary_key=True, index=True)
    flight_id = Column(Integer, ForeignKey("flights.id"), nullable=False)
    crew_member_id = Column(Integer, ForeignKey("crew_members.id"), nullable=False)
    role = Column(String, nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CrewDutyLog(Base):
    __tablename__ = "crew_duty_logs"
    id = Column(Integer, primary_key=True, index=True)
    crew_member_id = Column(Integer, ForeignKey("crew_members.id"), nullable=False)
    duty_start_utc = Column(DateTime, nullable=False)
    duty_end_utc = Column(DateTime, nullable=False)
    flight_id = Column(Integer, ForeignKey("flights.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CrewTrainingRecord(Base):
    __tablename__ = "crew_training_records"
    id = Column(Integer, primary_key=True, index=True)
    crew_member_id = Column(Integer, ForeignKey("crew_members.id"), nullable=False)
    training_type = Column(String, nullable=False)
    completed_date = Column(Date, nullable=False)
    expiry_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Pydantic models
class ReassignCrewRequest(BaseModel):
    new_flight_number: str
    pilot_employee_id: str
    attendant_employee_id: str

class ReassignCrewResponse(BaseModel):
    success: bool
    message: str
    reassigned_pilot: Optional[dict]
    reassigned_attendant: Optional[dict]

class RestRequirementResponse(BaseModel):
    meets_rest_requirement: bool
    last_duty_end: str
    rest_period_hours: float

class TrainingStatusResponse(BaseModel):
    has_valid_training: bool
    training_completed_date: Optional[str]
    training_expiry_date: Optional[str]

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/flights/{flight_number}/reassign-crew", response_model=ReassignCrewResponse, operation_id="reassign_crew_for_flight")
def reassign_crew_for_flight(
    flight_number: str,
    request: ReassignCrewRequest,
    db = Depends(get_db)
):
    # Get current user from environment
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    
    # Find the canceled flight
    canceled_flight = db.query(Flight).filter(Flight.flight_number == flight_number).first()
    if not canceled_flight:
        raise HTTPException(status_code=404, detail="Canceled flight not found")
    
    if canceled_flight.status != "Canceled":
        raise HTTPException(status_code=400, detail="Flight must be canceled to reassign crew")
    
    # Find the new flight
    new_flight = db.query(Flight).filter(Flight.flight_number == request.new_flight_number).first()
    if not new_flight:
        raise HTTPException(status_code=404, detail="New flight not found")
    
    # Find pilot and attendant
    pilot = db.query(CrewMember).filter(CrewMember.employee_id == request.pilot_employee_id).first()
    if not pilot:
        raise HTTPException(status_code=404, detail="Pilot not found")
    
    attendant = db.query(CrewMember).filter(CrewMember.employee_id == request.attendant_employee_id).first()
    if not attendant:
        raise HTTPException(status_code=404, detail="Flight attendant not found")
    
    # Check pilot rest requirement
    pilot_meets_rest = check_pilot_rest_requirement(db, pilot.id)
    if not pilot_meets_rest:
        raise HTTPException(status_code=400, detail="Pilot does not meet minimum rest requirement")
    
    # Check attendant training status
    attendant_has_valid_training = check_attendant_training_status(db, attendant.id)
    if not attendant_has_valid_training:
        raise HTTPException(status_code=400, detail="Flight attendant does not have valid recurrent security training")
    
    # Remove pilot and attendant from canceled flight
    old_assignments = db.query(FlightCrewAssignment).filter(
        and_(
            FlightCrewAssignment.flight_id == canceled_flight.id,
            FlightCrewAssignment.crew_member_id.in_([pilot.id, attendant.id])
        )
    ).all()
    
    for assignment in old_assignments:
        db.delete(assignment)
    
    # Assign pilot and attendant to new flight
    new_pilot_assignment = FlightCrewAssignment(
        flight_id=new_flight.id,
        crew_member_id=pilot.id,
        role=pilot.role
    )
    db.add(new_pilot_assignment)
    
    new_attendant_assignment = FlightCrewAssignment(
        flight_id=new_flight.id,
        crew_member_id=attendant.id,
        role=attendant.role
    )
    db.add(new_attendant_assignment)
    
    db.commit()
    
    # Get user details for response
    pilot_user = db.query(User).filter(User.id == pilot.user_id).first()
    attendant_user = db.query(User).filter(User.id == attendant.user_id).first()
    
    return ReassignCrewResponse(
        success=True,
        message=f"Crew successfully reassigned from {flight_number} to {request.new_flight_number}",
        reassigned_pilot={
            "employee_id": pilot.employee_id,
            "full_name": pilot_user.full_name
        },
        reassigned_attendant={
            "employee_id": attendant.employee_id,
            "full_name": attendant_user.full_name
        }
    )

def check_pilot_rest_requirement(db, pilot_id):
    # Get the most recent duty end for the pilot
    latest_duty_log = db.query(CrewDutyLog).filter(
        CrewDutyLog.crew_member_id == pilot_id
    ).order_by(CrewDutyLog.duty_end_utc.desc()).first()
    
    if not latest_duty_log:
        return True  # No previous duty log, so rest requirement is met
    
    # Calculate the rest period in hours
    rest_period = datetime.utcnow() - latest_duty_log.duty_end_utc
    rest_hours = rest_period.total_seconds() / 3600
    
    return rest_hours >= 10

def check_attendant_training_status(db, attendant_id):
    # Find the most recent recurrent security training
    training_record = db.query(CrewTrainingRecord).filter(
        and_(
            CrewTrainingRecord.crew_member_id == attendant_id,
            CrewTrainingRecord.training_type == "Recurrent Security"
        )
    ).order_by(CrewTrainingRecord.completed_date.desc()).first()
    
    if not training_record:
        return False
    
    # Check if training is within the last 12 months
    today = datetime.utcnow().date()
    twelve_months_ago = today - timedelta(days=365)
    
    if training_record.completed_date < twelve_months_ago:
        return False
    
    return True

@app.get("/crew/rest-requirements", response_model=RestRequirementResponse, operation_id="check_rest_requirements")
def check_rest_requirements(
    crew_member_id: str,
    db = Depends(get_db)
):
    # Find the crew member
    crew_member = db.query(CrewMember).filter(CrewMember.employee_id == crew_member_id).first()
    if not crew_member:
        raise HTTPException(status_code=404, detail="Crew member not found")
    
    # Get the most recent duty end for the crew member
    latest_duty_log = db.query(CrewDutyLog).filter(
        CrewDutyLog.crew_member_id == crew_member.id
    ).order_by(CrewDutyLog.duty_end_utc.desc()).first()
    
    if not latest_duty_log:
        # No previous duty log, so rest requirement is met
        return RestRequirementResponse(
            meets_rest_requirement=True,
            last_duty_end=None,
            rest_period_hours=0
        )
    
    # Calculate the rest period in hours
    rest_period = datetime.utcnow() - latest_duty_log.duty_end_utc
    rest_hours = rest_period.total_seconds() / 3600
    
    return RestRequirementResponse(
        meets_rest_requirement=rest_hours >= 10,
        last_duty_end=latest_duty_log.duty_end_utc.isoformat(),
        rest_period_hours=round(rest_hours, 2)
    )

@app.get("/crew/training-status", response_model=TrainingStatusResponse, operation_id="check_training_status")
def check_training_status(
    crew_member_id: str,
    db = Depends(get_db)
):
    # Find the crew member
    crew_member = db.query(CrewMember).filter(CrewMember.employee_id == crew_member_id).first()
    if not crew_member:
        raise HTTPException(status_code=404, detail="Crew member not found")
    
    # Find the most recent recurrent security training
    training_record = db.query(CrewTrainingRecord).filter(
        and_(
            CrewTrainingRecord.crew_member_id == crew_member.id,
            CrewTrainingRecord.training_type == "Recurrent Security"
        )
    ).order_by(CrewTrainingRecord.completed_date.desc()).first()
    
    if not training_record:
        return TrainingStatusResponse(
            has_valid_training=False,
            training_completed_date=None,
            training_expiry_date=None
        )
    
    # Check if training is within the last 12 months
    today = datetime.utcnow().date()
    twelve_months_ago = today - timedelta(days=365)
    
    has_valid_training = training_record.completed_date >= twelve_months_ago
    
    return TrainingStatusResponse(
        has_valid_training=has_valid_training,
        training_completed_date=training_record.completed_date.isoformat() if training_record.completed_date else None,
        training_expiry_date=training_record.expiry_date.isoformat() if training_record.expiry_date else None
    )
