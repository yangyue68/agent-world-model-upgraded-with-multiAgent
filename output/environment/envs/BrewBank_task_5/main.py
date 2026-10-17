import os
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, REAL, ForeignKey, text, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Database setup
DATABASE_PATH = os.getenv("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class YeastStrain(Base):
    __tablename__ = "yeast_strains"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    lab = Column(String)
    attenuation_min = Column(REAL)
    attenuation_max = Column(REAL)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Batch(Base):
    __tablename__ = "batches"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    brew_date = Column(Date, nullable=False)
    yeast_strain_id = Column(Integer, ForeignKey("yeast_strains.id"), nullable=False)
    final_gravity = Column(REAL)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class TastingNote(Base):
    __tablename__ = "tasting_notes"
    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    note = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class BatchResponse(BaseModel):
    id: int
    name: str
    brew_date: str
    final_gravity: float

class TastingNoteRequest(BaseModel):
    note: str

class TastingNoteResponse(BaseModel):
    id: int
    batch_id: int
    user_id: int
    note: str
    created_at: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/batches", response_model=List[BatchResponse], operation_id="find_brewed_batches_with_yeast_and_gravity")
def find_brewed_batches_with_yeast_and_gravity(
    yeast_strain_name: str = Query(..., description="Name of the yeast strain to filter by"),
    min_final_gravity: float = Query(..., description="Minimum final gravity threshold"),
    months_back: int = Query(..., description="Number of months back to look for batches"),
    db: Session = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Calculate the date X months ago
    cutoff_date = datetime.utcnow().date() - timedelta(days=months_back * 30)
    
    # Find yeast strain ID
    yeast_strain = db.query(YeastStrain).filter(YeastStrain.name == yeast_strain_name).first()
    if not yeast_strain:
        raise HTTPException(status_code=404, detail="Yeast strain not found")
    
    # Query batches
    batches = db.query(Batch).filter(
        Batch.user_id == current_user_id,
        Batch.brew_date >= cutoff_date,
        Batch.yeast_strain_id == yeast_strain.id,
        Batch.final_gravity != None,
        Batch.final_gravity > min_final_gravity
    ).all()
    
    # Convert to response model
    result = []
    for batch in batches:
        result.append({
            "id": batch.id,
            "name": batch.name,
            "brew_date": batch.brew_date.isoformat(),
            "final_gravity": batch.final_gravity
        })
    
    return result

@app.post("/batches/{batch_id}/tasting_notes", response_model=TastingNoteResponse, operation_id="add_tasting_note_to_batch")
def add_tasting_note_to_batch(
    batch_id: int,
    note_request: TastingNoteRequest,
    db: Session = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify batch exists and belongs to current user
    batch = db.query(Batch).filter(Batch.id == batch_id, Batch.user_id == current_user_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found or not owned by current user")
    
    # Create tasting note
    tasting_note = TastingNote(
        batch_id=batch_id,
        user_id=current_user_id,
        note=note_request.note
    )
    
    db.add(tasting_note)
    db.commit()
    db.refresh(tasting_note)
    
    return {
        "id": tasting_note.id,
        "batch_id": tasting_note.batch_id,
        "user_id": tasting_note.user_id,
        "note": tasting_note.note,
        "created_at": tasting_note.created_at.isoformat()
    }
