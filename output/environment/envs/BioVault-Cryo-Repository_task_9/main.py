import os
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, CheckConstraint, case
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

class AliquotEvent(Base):
    __tablename__ = "aliquot_events"
    
    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, unique=True, nullable=False)
    volume = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="active")
    void_reason = Column(Text)
    voided_by = Column(Integer, ForeignKey("users.id"))
    voided_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add check constraint for status
    __table_args__ = (
        CheckConstraint("status IN ('active', 'voided')", name="check_status"),
        CheckConstraint("volume > 0", name="check_volume"),
    )

class Vial(Base):
    __tablename__ = "vials"
    
    id = Column(Integer, primary_key=True, index=True)
    vial_id = Column(String, unique=True, nullable=False)
    aliquot_event_id = Column(Integer, ForeignKey("aliquot_events.id"), nullable=False)
    status = Column(String, nullable=False, default="active")
    destroyed_at = Column(DateTime)
    destroyed_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add check constraint for status
    __table_args__ = (
        CheckConstraint("status IN ('active', 'destroyed', 'voided')", name="check_vial_status"),
    )

class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    action = Column(String, nullable=False)
    comment = Column(Text)
    performed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class AliquotEventResponse(BaseModel):
    id: int
    transaction_id: str
    volume: float
    status: str
    void_reason: Optional[str] = None
    voided_by: Optional[int] = None
    voided_at: Optional[str] = None

class VialResponse(BaseModel):
    id: int
    vial_id: str
    aliquot_event_id: int
    status: str
    destroyed_at: Optional[str] = None
    destroyed_by: Optional[int] = None

class VoidAliquotEventRequest(BaseModel):
    void_reason: str
    voided_by: int

class VoidAliquotEventResponse(BaseModel):
    message: str
    aliquot_event_id: int

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/aliquot-events/{transaction_id}", response_model=AliquotEventResponse, operation_id="get_aliquot_event")
def get_aliquot_event(transaction_id: str, db = Depends(get_db)):
    event = db.query(AliquotEvent).filter(AliquotEvent.transaction_id == transaction_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Aliquot event not found")
    return event

@app.get("/vials", response_model=List[VialResponse], operation_id="find_derived_vials")
def find_derived_vials(aliquot_event_id: int, db = Depends(get_db)):
    vials = db.query(Vial).filter(Vial.aliquot_event_id == aliquot_event_id).all()
    return vials

@app.put("/aliquot-events/{transaction_id}/void", response_model=VoidAliquotEventResponse, operation_id="void_aliquot_event")
def void_aliquot_event(
    transaction_id: str,
    void_request: VoidAliquotEventRequest,
    db = Depends(get_db)
):
    # Get the current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Verify the voided_by matches the current user
    if void_request.voided_by != current_user_id:
        raise HTTPException(status_code=400, detail="Invalid voided_by user ID")
    
    # Get the aliquot event
    event = db.query(AliquotEvent).filter(AliquotEvent.transaction_id == transaction_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Aliquot event not found")
    
    if event.status == "voided":
        raise HTTPException(status_code=400, detail="Aliquot event is already voided")
    
    # Get all derived vials
    vials = db.query(Vial).filter(Vial.aliquot_event_id == event.id).all()
    
    # Check if all vials are destroyed
    destroyed_count = sum(1 for vial in vials if vial.status == "destroyed")
    total_count = len(vials)
    
    if destroyed_count != total_count:
        raise HTTPException(status_code=400, detail=f"Not all derived vials have been destroyed. Expected {total_count} destroyed, got {destroyed_count}")
    
    # Update the aliquot event
    event.status = "voided"
    event.void_reason = void_request.void_reason
    event.voided_by = void_request.voided_by
    event.voided_at = datetime.utcnow()
    
    # Create audit log
    audit_log = AuditLog(
        entity_type="aliquot_event",
        entity_id=event.id,
        action="voided",
        comment=void_request.void_reason,
        performed_by=current_user_id
    )
    db.add(audit_log)
    
    db.commit()
    db.refresh(event)
    
    return VoidAliquotEventResponse(
        message="Aliquot event successfully voided",
        aliquot_event_id=event.id
    )
