import os
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

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
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    trim = Column(String)
    year = Column(Integer, nullable=False)
    mileage = Column(Integer)
    color = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class TradeInAppraisal(Base):
    __tablename__ = "trade_in_appraisals"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    appraised_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    appraised_value = Column(Float, nullable=False)
    appraisal_date = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("appraised_value >= 0"),
        CheckConstraint("status IN ('pending', 'completed', 'expired', 'cancelled')"),
    )

class AppraisalNote(Base):
    __tablename__ = "appraisal_notes"
    id = Column(Integer, primary_key=True, index=True)
    appraisal_id = Column(Integer, ForeignKey("trade_in_appraisals.id"), nullable=False)
    note = Column(Text, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class TradeInAppraisalUpdate(BaseModel):
    appraised_value: float

class AppraisalNoteCreate(BaseModel):
    appraisal_id: int
    note: str

class TradeInAppraisalResponse(BaseModel):
    id: int
    vehicle_id: int
    appraised_by_user_id: int
    appraised_value: float
    appraisal_date: datetime
    status: Literal["pending", "completed", "expired", "cancelled"]
    created_at: datetime
    updated_at: datetime

class AppraisalNoteResponse(BaseModel):
    id: int
    appraisal_id: int
    note: str
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/trade-in-appraisals/{appraisal_id}", response_model=TradeInAppraisalResponse, operation_id="update_trade_in_appraisal")
def update_trade_in_appraisal(appraisal_id: int, appraisal_update: TradeInAppraisalUpdate, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    appraisal = db.query(TradeInAppraisal).filter(TradeInAppraisal.id == appraisal_id).first()
    if not appraisal:
        raise HTTPException(status_code=404, detail="Trade-in appraisal not found")
    
    appraisal.appraised_value = appraisal_update.appraised_value
    appraisal.status = "completed"
    appraisal.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(appraisal)
    
    return appraisal

@app.post("/appraisal-notes", response_model=AppraisalNoteResponse, operation_id="create_appraisal_note")
def create_appraisal_note(note_create: AppraisalNoteCreate, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the appraisal exists
    appraisal = db.query(TradeInAppraisal).filter(TradeInAppraisal.id == note_create.appraisal_id).first()
    if not appraisal:
        raise HTTPException(status_code=404, detail="Trade-in appraisal not found")
    
    note = AppraisalNote(
        appraisal_id=note_create.appraisal_id,
        note=note_create.note,
        created_by_user_id=current_user_id
    )
    
    db.add(note)
    db.commit()
    db.refresh(note)
    
    return note
