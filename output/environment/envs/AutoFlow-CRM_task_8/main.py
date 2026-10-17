import os
import re
from datetime import datetime
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, unique=True)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class PipelineStage(Base):
    __tablename__ = "pipeline_stages"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    stage_id = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class LeadNote(Base):
    __tablename__ = "lead_notes"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    note = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

# FastAPI app
app = FastAPI()

# Request models
class MoveLeadStageRequest(BaseModel):
    stage_id: int

class AddLeadNoteRequest(BaseModel):
    note: str

class MoveLeadStageResponse(BaseModel):
    message: str
    lead_id: int
    stage_id: int

class AddLeadNoteResponse(BaseModel):
    message: str
    note_id: int
    lead_id: int

class FindLeadByNameResponse(BaseModel):
    id: int
    name: str

class FindStageByNameResponse(BaseModel):
    id: int
    name: str

# Helper functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def parse_iso_datetime(dt_str: str) -> datetime:
    # Handle both T and space separators
    dt_str = re.sub(r'T', ' ', dt_str)
    return datetime.fromisoformat(dt_str)

# API Endpoints
@app.get("/leads/by-name/{name}", response_model=FindLeadByNameResponse, operation_id="find_lead_by_name")
def find_lead_by_name(name: str, db = Depends(get_db)):
    stmt = select(Lead.id, Lead.name).where(Lead.name == name)
    result = db.execute(stmt).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Lead not found")
    return FindLeadByNameResponse(id=result[0], name=result[1])

@app.get("/pipeline-stages/by-name/{name}", response_model=FindStageByNameResponse, operation_id="find_stage_by_name")
def find_stage_by_name(name: str, db = Depends(get_db)):
    stmt = select(PipelineStage.id, PipelineStage.name).where(PipelineStage.name == name)
    result = db.execute(stmt).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Pipeline stage not found")
    return FindStageByNameResponse(id=result[0], name=result[1])

@app.put("/leads/{lead_id}/stage", response_model=MoveLeadStageResponse, operation_id="move_lead_stage")
def move_lead_stage(lead_id: int, request: MoveLeadStageRequest, db = Depends(get_db)):
    # Verify lead exists
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Verify stage exists
    stage = db.query(PipelineStage).filter(PipelineStage.id == request.stage_id).first()
    if not stage:
        raise HTTPException(status_code=404, detail="Pipeline stage not found")
    
    # Update lead stage
    lead.stage_id = request.stage_id
    db.commit()
    db.refresh(lead)
    
    return MoveLeadStageResponse(
        message="Lead stage updated successfully",
        lead_id=lead.id,
        stage_id=stage.id
    )

@app.post("/leads/{lead_id}/notes", response_model=AddLeadNoteResponse, operation_id="add_lead_note")
def add_lead_note(lead_id: int, note: AddLeadNoteRequest, db = Depends(get_db)):
    # Verify lead exists
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    
    # Create note
    new_note = LeadNote(
        lead_id=lead_id,
        user_id=current_user_id,
        note=note.note
    )
    db.add(new_note)
    db.commit()
    db.refresh(new_note)
    
    return AddLeadNoteResponse(
        message="Note added successfully",
        note_id=new_note.id,
        lead_id=lead.id
    )

# Task execution endpoint
@app.post("/execute-task", response_model=dict)
def execute_task(db = Depends(get_db)):
    try:
        # Step 1: Find lead 'John Smith'
        stmt = select(Lead.id).where(Lead.name == "John Smith")
        lead_result = db.execute(stmt).fetchone()
        if not lead_result:
            raise HTTPException(status_code=404, detail="Lead 'John Smith' not found")
        lead_id = lead_result[0]
        
        # Step 2: Find 'Qualified' stage
        stmt = select(PipelineStage.id).where(PipelineStage.name == "Qualified")
        qualified_stage_result = db.execute(stmt).fetchone()
        if not qualified_stage_result:
            raise HTTPException(status_code=404, detail="'Qualified' stage not found")
        qualified_stage_id = qualified_stage_result[0]
        
        # Step 3: Find 'Negotiation' stage
        stmt = select(PipelineStage.id).where(PipelineStage.name == "Negotiation")
        negotiation_stage_result = db.execute(stmt).fetchone()
        if not negotiation_stage_result:
            raise HTTPException(status_code=404, detail="'Negotiation' stage not found")
        negotiation_stage_id = negotiation_stage_result[0]
        
        # Step 4: Move lead from 'Qualified' to 'Negotiation'
        stmt = text("UPDATE leads SET stage_id = :stage_id WHERE id = :lead_id")
        db.execute(stmt, {"stage_id": negotiation_stage_id, "lead_id": lead_id})
        db.commit()
        
        # Step 5: Add note about discount request
        note_content = "Customer requested a $2,000 discount on the 2022 Honda Accord."
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
        new_note = LeadNote(
            lead_id=lead_id,
            user_id=current_user_id,
            note=note_content
        )
        db.add(new_note)
        db.commit()
        db.refresh(new_note)
        
        return {
            "message": "Task completed successfully",
            "lead_id": lead_id,
            "stage_id": negotiation_stage_id,
            "note_id": new_note.id
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
