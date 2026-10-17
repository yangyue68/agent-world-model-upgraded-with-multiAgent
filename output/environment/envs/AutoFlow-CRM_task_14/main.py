import os
from datetime import datetime, timedelta
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, case
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("status IN ('New', 'Contacted', 'Qualified', 'Negotiation', 'Closed Won', 'Closed Lost')"),)

class LeadStatusHistory(Base):
    __tablename__ = "lead_status_history"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    old_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow)

class Reminder(Base):
    __tablename__ = "reminders"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reminder_date = Column(DateTime, nullable=False)
    description = Column(String)
    completed = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("completed IN (0, 1)"),)

# Pydantic Models
class UpdateLeadStatusRequest(BaseModel):
    status: Literal["Qualified"]

class CreateReminderRequest(BaseModel):
    lead_id: int
    reminder_date: str
    description: str | None = None

class UpdateLeadStatusResponse(BaseModel):
    success: bool
    message: str

class CreateReminderResponse(BaseModel):
    success: bool
    message: str
    reminder_id: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/leads/{lead_id}/status", response_model=UpdateLeadStatusResponse, operation_id="update_lead_status")
def update_lead_status(lead_id: int, status_request: UpdateLeadStatusRequest, db = Depends(get_db)):
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Find the lead
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Validate status transition
    if lead.status != "Negotiation":
        raise HTTPException(status_code=400, detail="Lead is not in Negotiation status")
    
    if status_request.status != "Qualified":
        raise HTTPException(status_code=400, detail="Invalid status. Only 'Qualified' is allowed.")
    
    # Record the status change
    old_status = lead.status
    new_status = status_request.status
    
    # Update lead status
    lead.status = new_status
    lead.updated_at = datetime.utcnow()
    
    # Add to history
    status_change = LeadStatusHistory(
        lead_id=lead.id,
        old_status=old_status,
        new_status=new_status,
        changed_by=current_user_id
    )
    db.add(status_change)
    
    db.commit()
    db.refresh(lead)
    
    return UpdateLeadStatusResponse(
        success=True,
        message=f"Lead status updated from {old_status} to {new_status}"
    )

@app.post("/reminders", response_model=CreateReminderResponse, operation_id="create_follow_up_reminder")
def create_follow_up_reminder(reminder_request: CreateReminderRequest, db = Depends(get_db)):
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Parse reminder date
    try:
        # Handle both T...Z and space-separated formats
        if "T" in reminder_request.reminder_date:
            reminder_datetime = datetime.fromisoformat(reminder_request.reminder_date.replace('Z', '+00:00'))
        else:
            reminder_datetime = datetime.fromisoformat(reminder_request.reminder_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    # Verify lead exists
    lead = db.query(Lead).filter(Lead.id == reminder_request.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Create reminder
    reminder = Reminder(
        lead_id=reminder_request.lead_id,
        user_id=current_user_id,
        reminder_date=reminder_datetime,
        description=reminder_request.description
    )
    
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    
    return CreateReminderResponse(
        success=True,
        message="Reminder created successfully",
        reminder_id=reminder.id
    )
