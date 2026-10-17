import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, CheckConstraint, func
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
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    phone = Column(String)
    role = Column(String, nullable=False, check_constraint="role IN ('salesperson', 'manager', 'admin')")
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    source = Column(String)
    status = Column(String, nullable=False, default="new", check_constraint="status IN ('new', 'contacted', 'qualified', 'test_drive_scheduled', 'negotiating', 'won', 'lost')")
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False, check_constraint="year >= 1900 AND year <= 2100")
    trim = Column(String)
    color = Column(String)
    mileage = Column(Integer, check_constraint="mileage >= 0")
    price = Column(Integer, check_constraint="price >= 0")
    status = Column(String, nullable=False, default="available", check_constraint="status IN ('available', 'reserved', 'sold', 'in_service')")
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())

class TestDrive(Base):
    __tablename__ = "test_drives"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    salesperson_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=30, check_constraint="duration_minutes > 0")
    status = Column(String, nullable=False, default="scheduled", check_constraint="status IN ('scheduled', 'completed', 'cancelled', 'no_show')")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())

class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"))
    test_drive_id = Column(Integer, ForeignKey("test_drives.id"))
    content = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    __table_args__ = (CheckConstraint("lead_id IS NOT NULL OR test_drive_id IS NOT NULL"),)

# Pydantic Models
class TestDriveCreate(BaseModel):
    lead_id: int
    vehicle_id: int
    salesperson_id: int
    scheduled_at: str
    duration_minutes: Optional[int] = 30

class TestDriveResponse(BaseModel):
    id: int
    lead_id: int
    vehicle_id: int
    salesperson_id: int
    scheduled_at: str
    duration_minutes: int
    status: str
    created_by: int
    created_at: str
    updated_at: str

class NoteCreate(BaseModel):
    test_drive_id: int
    content: str
    created_by: int

class NoteResponse(BaseModel):
    id: int
    lead_id: int
    test_drive_id: int
    content: str
    created_by: int
    created_at: str
    updated_at: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/test_drives", response_model=TestDriveResponse, operation_id="schedule_test_drive")
def schedule_test_drive(test_drive: TestDriveCreate, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Validate that the lead exists
    lead = db.query(Lead).filter(Lead.id == test_drive.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Validate that the vehicle exists
    vehicle = db.query(Vehicle).filter(Vehicle.id == test_drive.vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Validate that the salesperson exists
    salesperson = db.query(User).filter(User.id == test_drive.salesperson_id).first()
    if not salesperson:
        raise HTTPException(status_code=404, detail="Salesperson not found")
    
    # Parse scheduled_at
    scheduled_at = datetime.fromisoformat(test_drive.scheduled_at.replace('Z', '+00:00'))
    
    # Create test drive
    db_test_drive = TestDrive(
        lead_id=test_drive.lead_id,
        vehicle_id=test_drive.vehicle_id,
        salesperson_id=test_drive.salesperson_id,
        scheduled_at=scheduled_at,
        duration_minutes=test_drive.duration_minutes,
        created_by=current_user_id
    )
    
    db.add(db_test_drive)
    db.commit()
    db.refresh(db_test_drive)
    
    # Update lead status
    lead.status = "test_drive_scheduled"
    db.commit()
    
    return db_test_drive

@app.post("/notes", response_model=NoteResponse, operation_id="add_note_to_test_drive")
def add_note_to_test_drive(note: NoteCreate, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Validate that the test drive exists
    test_drive = db.query(TestDrive).filter(TestDrive.id == note.test_drive_id).first()
    if not test_drive:
        raise HTTPException(status_code=404, detail="Test drive not found")
    
    # Create note
    db_note = Note(
        lead_id=test_drive.lead_id,
        test_drive_id=note.test_drive_id,
        content=note.content,
        created_by=current_user_id
    )
    
    db.add(db_note)
    db.commit()
    db.refresh(db_note)
    
    return db_note
