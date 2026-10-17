import os
from datetime import datetime
from typing import Optional, Generator
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, CheckConstraint, case
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

class Animal(Base):
    __tablename__ = "animals"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    species = Column(String, nullable=False)
    breed = Column(String)
    age_years = Column(Integer)
    microchip = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Intake(Base):
    __tablename__ = "intakes"
    id = Column(Integer, primary_key=True)
    intake_number = Column(String, unique=True, nullable=False)
    animal_id = Column(Integer, ForeignKey("animals.id"), nullable=False)
    intake_date = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="active")
    voided_reason = Column(Text)
    voided_at = Column(DateTime)
    voided_by_user_id = Column(Integer, ForeignKey("users.id"))
    duplicate_of_intake_id = Column(Integer, ForeignKey("intakes.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('active','voided','completed','transferred')"),
    )

class MedicalEvent(Base):
    __tablename__ = "medical_events"
    id = Column(Integer, primary_key=True)
    intake_id = Column(Integer, ForeignKey("intakes.id"), nullable=False)
    event_type = Column(String, nullable=False)
    description = Column(Text)
    event_date = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class FosterPlacement(Base):
    __tablename__ = "foster_placements"
    id = Column(Integer, primary_key=True)
    placement_number = Column(String, unique=True, nullable=False)
    intake_id = Column(Integer, ForeignKey("intakes.id"), nullable=False)
    foster_user_id = Column(Integer, ForeignKey("users.id"))
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('active','completed','cancelled')"),
    )

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    details = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class IntakeLookupResponse(BaseModel):
    id: int
    intake_number: str
    animal_id: int
    status: str
    duplicate_of_intake_id: Optional[int]
    voided_reason: Optional[str]

class ReassignMedicalEventsRequest(BaseModel):
    source_intake_id: int

class ReassignMedicalEventsResponse(BaseModel):
    success: bool
    events_reassigned_count: int

class ReassignFosterPlacementRequest(BaseModel):
    target_intake_id: int

class ReassignFosterPlacementResponse(BaseModel):
    success: bool
    placement_id: int

class VoidDuplicateIntakeRequest(BaseModel):
    duplicate_of_intake_id: int
    voided_reason: str

class VoidDuplicateIntakeResponse(BaseModel):
    success: bool
    intake_id: int
    status: str

# FastAPI App
app = FastAPI()

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/intakes/lookup", response_model=IntakeLookupResponse, operation_id="lookup_intake_by_number")
def lookup_intake_by_number(intake_number: str, db: Session = Depends(get_db)):
    intake = db.query(Intake).filter(Intake.intake_number == intake_number).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found")
    
    return IntakeLookupResponse(
        id=intake.id,
        intake_number=intake.intake_number,
        animal_id=intake.animal_id,
        status=intake.status,
        duplicate_of_intake_id=intake.duplicate_of_intake_id,
        voided_reason=intake.voided_reason
    )

@app.put("/intakes/{id}/reassign-medical-events", response_model=ReassignMedicalEventsResponse, operation_id="reassign_medical_events_to_intake")
def reassign_medical_events_to_intake(id: int, request: ReassignMedicalEventsRequest, db: Session = Depends(get_db)):
    # Get target intake
    target_intake = db.query(Intake).filter(Intake.id == id).first()
    if not target_intake:
        raise HTTPException(status_code=404, detail="Target intake not found")
    
    # Get source intake
    source_intake = db.query(Intake).filter(Intake.id == request.source_intake_id).first()
    if not source_intake:
        raise HTTPException(status_code=404, detail="Source intake not found")
    
    # Count medical events before update
    count_before = db.query(MedicalEvent).filter(MedicalEvent.intake_id == request.source_intake_id).count()
    
    # Update medical events
    db.query(MedicalEvent).filter(MedicalEvent.intake_id == request.source_intake_id).update({"intake_id": id})
    db.commit()
    
    # Count medical events after update
    count_after = db.query(MedicalEvent).filter(MedicalEvent.intake_id == id).count()
    
    return ReassignMedicalEventsResponse(
        success=True,
        events_reassigned_count=count_after - count_before
    )

@app.put("/foster-placements/reassign", response_model=ReassignFosterPlacementResponse, operation_id="reassign_foster_placement_to_intake")
def reassign_foster_placement_to_intake(placement_number: str, request: ReassignFosterPlacementRequest, db: Session = Depends(get_db)):
    # Find foster placement
    foster_placement = db.query(FosterPlacement).filter(FosterPlacement.placement_number == placement_number).first()
    if not foster_placement:
        raise HTTPException(status_code=404, detail="Foster placement not found")
    
    # Get target intake
    target_intake = db.query(Intake).filter(Intake.id == request.target_intake_id).first()
    if not target_intake:
        raise HTTPException(status_code=404, detail="Target intake not found")
    
    # Update foster placement
    foster_placement.intake_id = request.target_intake_id
    db.commit()
    
    return ReassignFosterPlacementResponse(
        success=True,
        placement_id=foster_placement.id
    )

@app.post("/intakes/{id}/void-duplicate", response_model=VoidDuplicateIntakeResponse, operation_id="void_duplicate_intake")
def void_duplicate_intake(id: int, request: VoidDuplicateIntakeRequest, db: Session = Depends(get_db)):
    # Get the intake to be voided
    intake_to_void = db.query(Intake).filter(Intake.id == id).first()
    if not intake_to_void:
        raise HTTPException(status_code=404, detail="Intake to void not found")
    
    # Get the original intake
    original_intake = db.query(Intake).filter(Intake.id == request.duplicate_of_intake_id).first()
    if not original_intake:
        raise HTTPException(status_code=404, detail="Original intake not found")
    
    # Set current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    
    # Update the intake to void it
    intake_to_void.status = "voided"
    intake_to_void.voided_reason = request.voided_reason
    intake_to_void.voided_at = datetime.utcnow()
    intake_to_void.voided_by_user_id = current_user_id
    intake_to_void.duplicate_of_intake_id = request.duplicate_of_intake_id
    
    db.commit()
    
    return VoidDuplicateIntakeResponse(
        success=True,
        intake_id=intake_to_void.id,
        status=intake_to_void.status
    )
