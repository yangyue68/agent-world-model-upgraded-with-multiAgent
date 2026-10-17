import os
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
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
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    phone = Column(String)
    role = Column(String, default="salesperson")
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class LeadSource(Base):
    __tablename__ = "lead_sources"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)
    lead_source_id = Column(Integer)
    assigned_to = Column(Integer)
    status = Column(String, default="new")
    notes = Column(Text)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

# Pydantic models
class LeadCreate(BaseModel):
    full_name: str
    phone: str
    email: str
    lead_source_name: str
    assigned_to_full_name: str

class LeadResponse(BaseModel):
    id: int
    full_name: str
    phone: str
    email: str
    lead_source_id: int
    assigned_to: int
    status: str
    created_by: int
    created_at: str

class LeadSourceSearchResponse(BaseModel):
    id: int
    name: str

class UserSearchResponse(BaseModel):
    id: int
    full_name: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/leads", response_model=LeadResponse, operation_id="create_lead")
def create_lead(lead: LeadCreate, db = None):
    if db is None:
        db = next(get_db())
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if current_user_id == 0:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Find lead source by name
    lead_source = db.execute(
        select(LeadSource.id).where(LeadSource.name == lead.lead_source_name)
    ).scalar_one_or_none()
    
    if not lead_source:
        raise HTTPException(status_code=404, detail="Lead source not found")
    
    # Find user by full name
    user = db.execute(
        select(User.id).where(User.full_name == lead.assigned_to_full_name)
    ).scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Create new lead
    db_lead = Lead(
        full_name=lead.full_name,
        phone=lead.phone,
        email=lead.email,
        lead_source_id=lead_source,
        assigned_to=user,
        created_by=current_user_id,
        status="new"
    )
    
    db.add(db_lead)
    db.commit()
    db.refresh(db_lead)
    
    return LeadResponse(
        id=db_lead.id,
        full_name=db_lead.full_name,
        phone=db_lead.phone,
        email=db_lead.email,
        lead_source_id=db_lead.lead_source_id,
        assigned_to=db_lead.assigned_to,
        status=db_lead.status,
        created_by=db_lead.created_by,
        created_at=db_lead.created_at.isoformat() if db_lead.created_at else None
    )

@app.get("/lead_sources/search", response_model=LeadSourceSearchResponse, operation_id="search_lead_sources")
def search_lead_sources(name: str, db = None):
    if db is None:
        db = next(get_db())
    
    lead_source = db.execute(
        select(LeadSource.id, LeadSource.name).where(LeadSource.name == name)
    ).scalar_one_or_none()
    
    if not lead_source:
        raise HTTPException(status_code=404, detail="Lead source not found")
    
    return LeadSourceSearchResponse(
        id=lead_source[0],
        name=lead_source[1]
    )

@app.get("/users/search", response_model=UserSearchResponse, operation_id="search_users")
def search_users(full_name: str, db = None):
    if db is None:
        db = next(get_db())
    
    user = db.execute(
        select(User.id, User.full_name).where(User.full_name == full_name)
    ).scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserSearchResponse(
        id=user[0],
        full_name=user[1]
    )
