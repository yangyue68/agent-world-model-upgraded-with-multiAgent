import os
from datetime import date
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, REAL, DateTime, ForeignKey, func, case
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
    role = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Aircraft(Base):
    __tablename__ = "aircraft"
    id = Column(Integer, primary_key=True)
    registration = Column(String, unique=True, nullable=False)
    model = Column(String)
    serial_number = Column(String, unique=True)
    total_flight_hours = Column(REAL, default=0.0)
    last_100hr_inspection_date = Column(Date)
    last_100hr_inspection_hours = Column(REAL)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class MaintenanceAlert(Base):
    __tablename__ = "maintenance_alerts"
    id = Column(Integer, primary_key=True)
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"), nullable=False)
    alert_type = Column(String, nullable=False)
    description = Column(String, nullable=False)
    due_date = Column(Date)
    status = Column(String, default="open", check_constraint="status IN ('open', 'acknowledged', 'resolved', 'cancelled')")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class FAAForm337Draft(Base):
    __tablename__ = "faa_form_337_drafts"
    id = Column(Integer, primary_key=True)
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"), nullable=False)
    maintenance_alert_id = Column(Integer, ForeignKey("maintenance_alerts.id"), nullable=True)
    maintenance_type = Column(String, nullable=False)
    description = Column(String, nullable=False)
    scheduled_date = Column(Date, nullable=False)
    status = Column(String, default="draft", check_constraint="status IN ('draft', 'submitted', 'approved', 'rejected')")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

# Pydantic Models
class AircraftDetails(BaseModel):
    id: int
    registration: str
    model: str
    serial_number: str
    total_flight_hours: float
    last_100hr_inspection_date: date
    last_100hr_inspection_hours: float

class FAAForm337DraftRequest(BaseModel):
    maintenance_type: Literal["Inspection", "Repair", "Routine Maintenance"]
    description: str
    scheduled_date: date

class TriggerMaintenanceAlertResponse(BaseModel):
    alert_id: int
    form_337_id: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/aircraft/{registration}/maintenance-alert", response_model=TriggerMaintenanceAlertResponse, operation_id="trigger_maintenance_alert")
def trigger_maintenance_alert(registration: str, db: Session = Depends(get_db)):
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find aircraft by registration
    aircraft = db.query(Aircraft).filter(Aircraft.registration == registration).first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Calculate hours since last 100-hour inspection
    if aircraft.last_100hr_inspection_hours is None:
        raise HTTPException(status_code=400, detail="No previous 100-hour inspection recorded")
    
    hours_since_last_inspection = aircraft.total_flight_hours - aircraft.last_100hr_inspection_hours
    
    # Check if hours exceed 250
    if hours_since_last_inspection <= 250:
        raise HTTPException(status_code=400, detail="Flight hours since last 100-hour inspection are within limits")
    
    # Create maintenance alert
    alert = MaintenanceAlert(
        aircraft_id=aircraft.id,
        alert_type="100-Hour Inspection Overdue",
        description=f"100-hour inspection overdue by {hours_since_last_inspection:.1f} hours",
        due_date=date.today(),
        status="open",
        created_by=current_user_id
    )
    db.add(alert)
    db.flush()  # Get the ID without committing
    
    # Generate FAA Form 337 draft
    form_337 = FAAForm337Draft(
        aircraft_id=aircraft.id,
        maintenance_alert_id=alert.id,
        maintenance_type="Inspection",
        description="100-hour inspection overdue",
        scheduled_date=date(2024, 6, 15),
        status="draft",
        created_by=current_user_id
    )
    db.add(form_337)
    db.flush()  # Get the ID without committing
    
    db.commit()
    
    return TriggerMaintenanceAlertResponse(alert_id=alert.id, form_337_id=form_337.id)

@app.get("/aircraft/{registration}", response_model=AircraftDetails, operation_id="get_aircraft_details")
def get_aircraft_details(registration: str, db: Session = Depends(get_db)):
    aircraft = db.query(Aircraft).filter(Aircraft.registration == registration).first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    return AircraftDetails(
        id=aircraft.id,
        registration=aircraft.registration,
        model=aircraft.model,
        serial_number=aircraft.serial_number,
        total_flight_hours=aircraft.total_flight_hours,
        last_100hr_inspection_date=aircraft.last_100hr_inspection_date,
        last_100hr_inspection_hours=aircraft.last_100hr_inspection_hours
    )

@app.post("/aircraft/{registration}/faa-form-337-draft", response_model=FAAForm337DraftRequest, operation_id="generate_faa_form_337_draft")
def generate_faa_form_337_draft(registration: str, draft_request: FAAForm337DraftRequest, db: Session = Depends(get_db)):
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find aircraft by registration
    aircraft = db.query(Aircraft).filter(Aircraft.registration == registration).first()
    if not aircraft:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    # Create FAA Form 337 draft
    form_337 = FAAForm337Draft(
        aircraft_id=aircraft.id,
        maintenance_type=draft_request.maintenance_type,
        description=draft_request.description,
        scheduled_date=draft_request.scheduled_date,
        status="draft",
        created_by=current_user_id
    )
    db.add(form_337)
    db.commit()
    db.refresh(form_337)
    
    return FAAForm337DraftRequest(
        maintenance_type=form_337.maintenance_type,
        description=form_337.description,
        scheduled_date=form_337.scheduled_date
    )
