import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, func, case
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
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    role = Column(String, default="salesperson", nullable=False)
    is_active = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class LeadSource(Base):
    __tablename__ = "lead_sources"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    is_active = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=False)
    lead_source_id = Column(Integer, ForeignKey("lead_sources.id"), nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="new", nullable=False)
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class RoundRobinState(Base):
    __tablename__ = "round_robin_state"
    id = Column(Integer, primary_key=True, index=True)
    last_assigned_user_id = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class LeadAssignmentHistory(Base):
    __tablename__ = "lead_assignment_history"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    assignment_method = Column(String, default="round_robin", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

# Pydantic Models
class LeadCreate(BaseModel):
    full_name: str
    phone: str
    email: str
    lead_source_name: str
    created_by_user_id: int

class LeadResponse(BaseModel):
    id: int
    full_name: str
    phone: str
    email: str
    lead_source_id: int
    assigned_to: Optional[int]
    status: str
    created_by: int
    created_at: str

class LeadSourceResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    is_active: int
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

def get_current_user_id():
    return int(os.environ.get("AWM_CURRENT_USER_ID", 0))

@app.post("/leads", response_model=LeadResponse, operation_id="create_lead")
def create_lead(lead: LeadCreate, db: Session = Depends(get_db)):
    # Verify lead source exists and is active
    lead_source = db.query(LeadSource).filter(
        LeadSource.name == lead.lead_source_name,
        LeadSource.is_active == 1
    ).first()
    
    if not lead_source:
        raise HTTPException(status_code=404, detail="Lead source not found or inactive")
    
    # Get next salesperson via round-robin
    current_user_id = get_current_user_id()
    
    # Find the next salesperson in round-robin
    last_assigned = db.query(RoundRobinState.last_assigned_user_id).first()
    last_user_id = last_assigned[0] if last_assigned else None
    
    # Get all active salespeople
    salespeople = db.query(User).filter(
        User.role == "salesperson",
        User.is_active == 1
    ).order_by(User.id).all()
    
    if not salespeople:
        raise HTTPException(status_code=400, detail="No active salespeople available")
    
    # Determine next user in round-robin
    next_user_id = None
    if last_user_id is None:
        # First assignment - pick first salesperson
        next_user_id = salespeople[0].id
    else:
        # Find current user's position and get next
        try:
            current_index = next(i for i, user in enumerate(salespeople) if user.id == last_user_id)
            next_index = (current_index + 1) % len(salespeople)
            next_user_id = salespeople[next_index].id
        except StopIteration:
            # If last user not found, fallback to first
            next_user_id = salespeople[0].id
    
    # Create the lead
    db_lead = Lead(
        full_name=lead.full_name,
        phone=lead.phone,
        email=lead.email,
        lead_source_id=lead_source.id,
        assigned_to=next_user_id,
        created_by=current_user_id
    )
    
    db.add(db_lead)
    db.commit()
    db.refresh(db_lead)
    
    # Update round-robin state
    rr_state = db.query(RoundRobinState).first()
    if rr_state:
        rr_state.last_assigned_user_id = next_user_id
        rr_state.updated_at = datetime.utcnow()
    else:
        rr_state = RoundRobinState(last_assigned_user_id=next_user_id)
        db.add(rr_state)
    
    db.commit()
    
    # Record assignment history
    assignment_record = LeadAssignmentHistory(
        lead_id=db_lead.id,
        assigned_to=next_user_id,
        assigned_by=current_user_id,
        assignment_method="round_robin"
    )
    db.add(assignment_record)
    db.commit()
    
    # Return response
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

@app.get("/lead-sources", response_model=LeadSourceResponse, operation_id="find_lead_source_by_name")
def find_lead_source_by_name(name: str = Query(...), db: Session = Depends(get_db)):
    lead_source = db.query(LeadSource).filter(
        LeadSource.name == name,
        LeadSource.is_active == 1
    ).first()
    
    if not lead_source:
        raise HTTPException(status_code=404, detail="Lead source not found or inactive")
    
    return LeadSourceResponse(
        id=lead_source.id,
        name=lead_source.name,
        description=lead_source.description,
        is_active=lead_source.is_active,
        created_at=lead_source.created_at.isoformat() if lead_source.created_at else None,
        updated_at=lead_source.updated_at.isoformat() if lead_source.updated_at else None
    )

@app.get("/lead-sources/{name}", response_model=LeadSourceResponse, operation_id="get_lead_source_by_name")
def get_lead_source_by_name(name: str, db: Session = Depends(get_db)):
    lead_source = db.query(LeadSource).filter(
        LeadSource.name == name,
        LeadSource.is_active == 1
    ).first()
    
    if not lead_source:
        raise HTTPException(status_code=404, detail="Lead source not found or inactive")
    
    return LeadSourceResponse(
        id=lead_source.id,
        name=lead_source.name,
        description=lead_source.description,
        is_active=lead_source.is_active,
        created_at=lead_source.created_at.isoformat() if lead_source.created_at else None,
        updated_at=lead_source.updated_at.isoformat() if lead_source.updated_at else None
    )
