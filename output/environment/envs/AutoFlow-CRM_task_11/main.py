import os
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, text, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

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
    name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    description = Column(String, nullable=False)
    due_date = Column(DateTime, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class LeadResponse(BaseModel):
    id: int
    name: str
    status: str
    assigned_user_id: int

class LeadUpdateRequest(BaseModel):
    status: str

class TaskCreateRequest(BaseModel):
    lead_id: int
    description: str
    due_date: str

class TaskResponse(BaseModel):
    id: int
    lead_id: int
    assigned_user_id: int
    description: str
    due_date: str
    status: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/leads", response_model=List[LeadResponse], operation_id="find_leads_by_name")
def find_leads_by_name(name: str, db: Session = Depends(get_db)):
    leads = db.query(Lead).filter(Lead.name.like(f"%{name}%")).all()
    return leads

@app.put("/leads/{lead_id}", response_model=LeadResponse, operation_id="update_lead_status")
def update_lead_status(lead_id: int, status: LeadUpdateRequest, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    if lead.status != "Contact":
        raise HTTPException(status_code=400, detail="Lead status must be 'Contact' to update to 'Qualified'")
    
    if status.status != "Qualified":
        raise HTTPException(status_code=400, detail="Status must be 'Qualified'")
    
    lead.status = status.status
    db.commit()
    db.refresh(lead)
    return lead

@app.post("/tasks", response_model=TaskResponse, operation_id="create_follow_up_task")
def create_follow_up_task(task_data: TaskCreateRequest, db: Session = Depends(get_db)):
    # Get the lead to verify it exists and get assigned user
    lead = db.query(Lead).filter(Lead.id == task_data.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Parse due_date
    try:
        due_date = datetime.fromisoformat(task_data.due_date.replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    # Create task
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    task = Task(
        lead_id=task_data.lead_id,
        assigned_user_id=lead.assigned_user_id,
        description=task_data.description,
        due_date=due_date,
        status="pending"
    )
    
    db.add(task)
    db.commit()
    db.refresh(task)
    # Convert due_date to string to match expected response format
    task_response = TaskResponse(
        id=task.id,
        lead_id=task.lead_id,
        assigned_user_id=task.assigned_user_id,
        description=task.description,
        due_date=task.due_date.isoformat() + "Z",
        status=task.status
    )
    return task_response
