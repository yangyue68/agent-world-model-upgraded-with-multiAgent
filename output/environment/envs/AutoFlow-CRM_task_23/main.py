import os
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, text, case
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lead(Base):
    __tablename__ = "leads"
    
    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, nullable=False)
    stage = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Relationship
    user = relationship("User")

class Note(Base):
    __tablename__ = "notes"
    
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    content = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Relationship
    user = relationship("User")

# Pydantic models
class LeadResponse(BaseModel):
    id: int
    source: str
    stage: str
    created_at: datetime

class LeadDetailsResponse(BaseModel):
    id: int
    source: str
    stage: str
    created_at: datetime
    updated_at: datetime
    user_id: int

class NoteRequest(BaseModel):
    content: str

class NoteResponse(BaseModel):
    id: int
    lead_id: int
    content: str
    created_at: datetime
    updated_at: datetime
    user_id: int

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/leads", response_model=List[LeadResponse], operation_id="find_leads_by_source_and_stage")
def find_leads_by_source_and_stage(
    source: str = Query(..., description="Lead source to filter by"),
    stage: str = Query(..., description="Lead stage to filter by"),
    db = Depends(get_db)
):
    # Calculate date 30 days ago
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    # Query leads with specified source and stage created in the last 30 days
    leads = db.query(Lead).filter(
        Lead.source == source,
        Lead.stage == stage,
        Lead.created_at >= thirty_days_ago
    ).all()
    
    return [
        LeadResponse(
            id=lead.id,
            source=lead.source,
            stage=lead.stage,
            created_at=lead.created_at
        ) for lead in leads
    ]

@app.get("/leads/{lead_id}", response_model=LeadDetailsResponse, operation_id="get_lead_details")
def get_lead_details(lead_id: int, db = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    return LeadDetailsResponse(
        id=lead.id,
        source=lead.source,
        stage=lead.stage,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        user_id=lead.user_id
    )

@app.post("/leads/{lead_id}/notes", response_model=NoteResponse, operation_id="add_note_to_lead")
def add_note_to_lead(
    lead_id: int,
    note_request: NoteRequest,
    db = Depends(get_db)
):
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Verify lead exists
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Create new note
    note = Note(
        lead_id=lead_id,
        content=note_request.content,
        user_id=current_user_id
    )
    
    db.add(note)
    db.commit()
    db.refresh(note)
    
    return NoteResponse(
        id=note.id,
        lead_id=note.lead_id,
        content=note.content,
        created_at=note.created_at,
        updated_at=note.updated_at,
        user_id=note.user_id
    )
