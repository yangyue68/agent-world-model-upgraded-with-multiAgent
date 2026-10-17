import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, case
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
    registration = Column(String, unique=True, nullable=False)
    model = Column(String)
    manufacturer = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class MaintenanceEvent(Base):
    __tablename__ = "maintenance_events"
    id = Column(Integer, primary_key=True)
    event_code = Column(String, unique=True, nullable=False)
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"), nullable=False)
    event_type = Column(String, nullable=False)
    status = Column(String, nullable=False)
    scheduled_date = Column(DateTime)
    completion_timestamp = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('Scheduled', 'In Progress', 'Completed', 'Cancelled')"),
    )

class WeightBalanceRecalculation(Base):
    __tablename__ = "weight_balance_recalculations"
    id = Column(Integer, primary_key=True)
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"), nullable=False)
    maintenance_event_id = Column(Integer, ForeignKey("maintenance_events.id"), nullable=True)
    status = Column(String, nullable=False)
    recalculated_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'completed', 'not_required')"),
    )

class FlightAssignment(Base):
    __tablename__ = "flight_assignments"
    id = Column(Integer, primary_key=True)
    aircraft_id = Column(Integer, ForeignKey("aircraft.id"), nullable=False)
    flight_number = Column(String, nullable=False)
    departure_time = Column(DateTime)
    arrival_time = Column(DateTime)
    status = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('scheduled', 'in_flight', 'completed', 'cancelled')"),
    )

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    details = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class MaintenanceEventUpdateRequest(BaseModel):
    status: str
    completion_timestamp: str

class MaintenanceEventResponse(BaseModel):
    id: int
    event_code: str
    status: str
    completion_timestamp: Optional[str]

class WeightBalanceRecalculationResponse(BaseModel):
    id: int
    status: str

class UpdateMaintenanceEventResponse(BaseModel):
    success: bool
    message: str
    updated_event: MaintenanceEventResponse
    flagged_recalculations: List[WeightBalanceRecalculationResponse]

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/maintenance-events/{event_id}", response_model=UpdateMaintenanceEventResponse, operation_id="update_maintenance_event_status")
def update_maintenance_event_status(
    event_id: str,
    payload: MaintenanceEventUpdateRequest,
    db: Session = Depends(get_db)
):
    # Validate status
    if payload.status != "Completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be 'Completed'"
        )
    
    # Parse completion timestamp
    try:
        completion_dt = datetime.fromisoformat(payload.completion_timestamp.replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid timestamp format"
        )

    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find maintenance event
    event = db.query(MaintenanceEvent).filter(MaintenanceEvent.event_code == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Maintenance event not found"
        )
    
    # Verify event status is 'Scheduled'
    if event.status != "Scheduled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maintenance event status must be 'Scheduled' to update to 'Completed'"
        )
    
    # Update event status and completion timestamp
    event.status = "Completed"
    event.completion_timestamp = completion_dt
    
    # Flag associated weight & balance recalculations as pending
    wb_recalcs = db.query(WeightBalanceRecalculation).filter(
        WeightBalanceRecalculation.aircraft_id == event.aircraft_id
    ).all()
    
    flagged_recalcs = []
    for recalc in wb_recalcs:
        if recalc.status != "completed":
            recalc.status = "pending"
            flagged_recalcs.append(recalc)
    
    # Create audit log entry
    audit_entry = AuditLog(
        user_id=current_user_id,
        action="UPDATE",
        entity_type="maintenance_event",
        entity_id=event.id,
        details=f"Status changed to Completed for {event.event_code}"
    )
    db.add(audit_entry)
    
    # Commit changes
    db.commit()
    db.refresh(event)
    
    # Prepare response
    updated_event_response = MaintenanceEventResponse(
        id=event.id,
        event_code=event.event_code,
        status=event.status,
        completion_timestamp=event.completion_timestamp.isoformat() + "Z" if event.completion_timestamp else None
    )
    
    flagged_recalcs_response = [
        WeightBalanceRecalculationResponse(
            id=recalc.id,
            status=recalc.status
        ) for recalc in flagged_recalcs
    ]
    
    return UpdateMaintenanceEventResponse(
        success=True,
        message="Maintenance event updated successfully",
        updated_event=updated_event_response,
        flagged_recalculations=flagged_recalcs_response
    )
