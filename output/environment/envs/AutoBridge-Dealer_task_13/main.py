import os
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint
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
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lead(Base):
    __tablename__ = "leads"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String, nullable=False)
    customer_phone = Column(String)
    customer_email = Column(String)
    vehicle_interest = Column(String)
    status = Column(String, nullable=False, default="New")
    assigned_to = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("status IN ('New', 'Contacted', 'Qualified', 'Negotiation', 'Closed', 'Lost')", name="check_status"),
    )

class LeadNote(Base):
    __tablename__ = "lead_notes"
    
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    note = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class LeadStatusUpdate(BaseModel):
    status: Literal["New", "Contacted", "Qualified", "Negotiation", "Closed", "Lost"]

class LeadNoteCreate(BaseModel):
    note: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/leads/{lead_id}", response_model=dict, operation_id="update_lead_status")
async def update_lead_status(lead_id: int, status_update: LeadStatusUpdate, db = Depends(get_db)):
    # Get the current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find the lead
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Update the lead status
    lead.status = status_update.status
    lead.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(lead)
    
    return {"id": lead.id, "status": lead.status}

@app.post("/leads/{lead_id}/notes", response_model=dict, operation_id="add_lead_note")
async def add_lead_note(lead_id: int, note_create: LeadNoteCreate, db = Depends(get_db)):
    # Get the current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find the lead
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Create the note
    note = LeadNote(
        lead_id=lead_id,
        user_id=current_user_id,
        note=note_create.note
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    
    return {"id": note.id, "lead_id": note.lead_id, "note": note.note}
