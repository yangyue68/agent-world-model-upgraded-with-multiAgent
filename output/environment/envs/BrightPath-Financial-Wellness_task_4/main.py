from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
import os

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    display_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class BudgetEnvelope(Base):
    __tablename__ = "budget_envelopes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    limit_amount = Column(Float, nullable=False)
    month = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("limit_amount >= 0"),
    )

class BudgetAlert(Base):
    __tablename__ = "budget_alerts"
    id = Column(Integer, primary_key=True)
    envelope_id = Column(Integer, ForeignKey("budget_envelopes.id"), nullable=False)
    threshold_percent = Column(Float, nullable=False)
    is_active = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("threshold_percent > 0 AND threshold_percent <= 100"),
        CheckConstraint("is_active IN (0, 1)"),
    )

# Pydantic Models
class BudgetEnvelopeResponse(BaseModel):
    id: int
    user_id: int
    name: str
    limit_amount: float
    month: str
    created_at: str
    updated_at: str

class BudgetAlertResponse(BaseModel):
    id: int
    envelope_id: int
    threshold_percent: float
    is_active: int
    created_at: str
    updated_at: str

class UpdateBudgetEnvelopeRequest(BaseModel):
    limit_amount: float

class CreateBudgetAlertRequest(BaseModel):
    threshold_percent: float

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/budget_envelopes/{envelope_id}", response_model=BudgetEnvelopeResponse, operation_id="update_budget_envelope")
def update_budget_envelope(
    envelope_id: int,
    payload: UpdateBudgetEnvelopeRequest,
    db: Session = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    envelope = db.query(BudgetEnvelope).filter(
        BudgetEnvelope.id == envelope_id,
        BudgetEnvelope.user_id == current_user_id
    ).first()
    
    if not envelope:
        raise HTTPException(status_code=404, detail="Budget envelope not found")
    
    envelope.limit_amount = payload.limit_amount
    envelope.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(envelope)
    
    return envelope

@app.post("/budget_envelopes/{envelope_id}/alerts", response_model=BudgetAlertResponse, operation_id="create_budget_alert")
def create_budget_alert(
    envelope_id: int,
    payload: CreateBudgetAlertRequest,
    db: Session = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the envelope belongs to the current user
    envelope = db.query(BudgetEnvelope).filter(
        BudgetEnvelope.id == envelope_id,
        BudgetEnvelope.user_id == current_user_id
    ).first()
    
    if not envelope:
        raise HTTPException(status_code=404, detail="Budget envelope not found")
    
    alert = BudgetAlert(
        envelope_id=envelope_id,
        threshold_percent=payload.threshold_percent,
        is_active=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(alert)
    db.commit()
    db.refresh(alert)
    
    return alert
