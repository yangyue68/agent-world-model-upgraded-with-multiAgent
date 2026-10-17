from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

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
    role = Column(String, nullable=False, default="salesperson")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lead(Base):
    __tablename__ = "leads"
    
    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, nullable=False)
    contact_name = Column(String, nullable=False)
    contact_phone = Column(String, nullable=False)
    contact_email = Column(String)
    source = Column(String, nullable=False)
    status = Column(String, nullable=False, default="new")
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", foreign_keys=[assigned_to])
    creator = relationship("User", foreign_keys=[created_by])

class LeadVehicle(Base):
    __tablename__ = "lead_vehicles"
    
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    model_year = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Constraints
    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_quantity_positive"),
    )

# Pydantic models
class VehicleRequest(BaseModel):
    make: str
    model: str
    model_year: int
    quantity: int
    notes: Optional[str] = None

class CreateLeadRequest(BaseModel):
    company_name: str
    contact_name: str
    contact_phone: str
    contact_email: Optional[str] = None
    source: str
    assigned_to: int
    vehicles: List[VehicleRequest]

class CreateLeadResponse(BaseModel):
    lead_id: int
    message: str

# FastAPI app
app = FastAPI()

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/leads", response_model=CreateLeadResponse, operation_id="create_fleet_lead")
def create_fleet_lead(lead_data: CreateLeadRequest):
    db = next(get_db())
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Create the lead
    db_lead = Lead(
        company_name=lead_data.company_name,
        contact_name=lead_data.contact_name,
        contact_phone=lead_data.contact_phone,
        contact_email=lead_data.contact_email,
        source=lead_data.source,
        assigned_to=lead_data.assigned_to,
        created_by=current_user_id
    )
    
    db.add(db_lead)
    db.commit()
    db.refresh(db_lead)
    
    # Add vehicles
    for vehicle_data in lead_data.vehicles:
        db_vehicle = LeadVehicle(
            lead_id=db_lead.id,
            make=vehicle_data.make,
            model=vehicle_data.model,
            model_year=vehicle_data.model_year,
            quantity=vehicle_data.quantity,
            notes=vehicle_data.notes
        )
        db.add(db_vehicle)
    
    db.commit()
    
    return CreateLeadResponse(
        lead_id=db_lead.id,
        message="Lead created successfully"
    )
