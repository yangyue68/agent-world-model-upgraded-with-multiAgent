import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, REAL, TEXT, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

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

class Counterparty(Base):
    __tablename__ = "counterparties"
    id = Column(Integer, primary_key=True, index=True)
    lei = Column(String, unique=True, nullable=False)
    legal_name = Column(String, nullable=False)
    jurisdiction = Column(String)
    entity_type = Column(String)
    incorporation_date = Column(Date)
    registered_address = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class KYCAMLAssessment(Base):
    __tablename__ = "kyc_aml_assessments"
    id = Column(Integer, primary_key=True, index=True)
    counterparty_id = Column(Integer, ForeignKey("counterparties.id"), nullable=False)
    assessed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assessment_type = Column(String, nullable=False)
    risk_score = Column(REAL)
    risk_level = Column(String, server_default=None)
    compliance_status = Column(String, nullable=False)
    adverse_media_flag = Column(Integer, default=0)
    beneficial_ownership_confidence_score = Column(REAL)
    assessment_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    next_review_date = Column(DateTime)
    notes = Column(TEXT)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("risk_level IN ('low', 'medium', 'high', 'critical')", name="chk_risk_level"),
        CheckConstraint("compliance_status IN ('compliant', 'non_compliant', 'pending', 'under_review', 'expired')", name="chk_compliance_status"),
        CheckConstraint("adverse_media_flag IN (0, 1)", name="chk_adverse_media_flag"),
        CheckConstraint("beneficial_ownership_confidence_score >= 0 AND beneficial_ownership_confidence_score <= 1", name="chk_beneficial_ownership_confidence_score"),
    )

class AdverseMediaFlag(Base):
    __tablename__ = "adverse_media_flags"
    id = Column(Integer, primary_key=True, index=True)
    counterparty_id = Column(Integer, ForeignKey("counterparties.id"), nullable=False)
    source = Column(String, nullable=False)
    headline = Column(String, nullable=False)
    description = Column(TEXT)
    severity = Column(String)
    published_date = Column(Date)
    url = Column(String)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("severity IN ('low', 'medium', 'high', 'critical')", name="chk_severity"),
        CheckConstraint("is_active IN (0, 1)", name="chk_is_active"),
    )

class BeneficialOwnership(Base):
    __tablename__ = "beneficial_ownership"
    id = Column(Integer, primary_key=True, index=True)
    counterparty_id = Column(Integer, ForeignKey("counterparties.id"), nullable=False)
    owner_name = Column(String, nullable=False)
    ownership_percentage = Column(REAL)
    confidence_score = Column(REAL)
    verification_status = Column(String)
    last_verified_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("ownership_percentage >= 0 AND ownership_percentage <= 100", name="chk_ownership_percentage"),
        CheckConstraint("confidence_score >= 0 AND confidence_score <= 1", name="chk_confidence_score"),
        CheckConstraint("verification_status IN ('verified', 'unverified', 'pending', 'failed')", name="chk_verification_status"),
    )

# Pydantic Models
class AdverseMediaFlagResponse(BaseModel):
    id: int
    source: str
    headline: str
    severity: str
    published_date: str

class BeneficialOwnerResponse(BaseModel):
    owner_name: str
    ownership_percentage: float
    confidence_score: float
    verification_status: str

class CounterpartyResponse(BaseModel):
    id: int
    lei: str
    legal_name: str

class ComplianceStatusResponse(BaseModel):
    counterparty: CounterpartyResponse
    compliance_status: str
    risk_level: str
    adverse_media_flag: bool
    beneficial_ownership_confidence_score: float
    adverse_media_flags: List[AdverseMediaFlagResponse]
    beneficial_owners: List[BeneficialOwnerResponse]

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/counterparties/{lei}/compliance-status", response_model=ComplianceStatusResponse, operation_id="get_counterparty_compliance_status")
def get_counterparty_compliance_status(lei: str):
    db = next(get_db())
    
    # Find counterparty by LEI
    counterparty = db.query(Counterparty).filter(Counterparty.lei == lei).first()
    if not counterparty:
        raise HTTPException(status_code=404, detail="Counterparty not found")
    
    # Get latest assessment
    assessment = db.query(KYCAMLAssessment).filter(
        KYCAMLAssessment.counterparty_id == counterparty.id
    ).order_by(KYCAMLAssessment.assessment_date.desc()).first()
    
    if not assessment:
        raise HTTPException(status_code=404, detail="No assessment found for counterparty")
    
    # Get adverse media flags
    adverse_media_flags = db.query(AdverseMediaFlag).filter(
        AdverseMediaFlag.counterparty_id == counterparty.id,
        AdverseMediaFlag.is_active == 1
    ).all()
    
    # Get beneficial owners
    beneficial_owners = db.query(BeneficialOwnership).filter(
        BeneficialOwnership.counterparty_id == counterparty.id
    ).all()
    
    # Prepare response data
    response_data = {
        "counterparty": {
            "id": counterparty.id,
            "lei": counterparty.lei,
            "legal_name": counterparty.legal_name
        },
        "compliance_status": assessment.compliance_status,
        "risk_level": assessment.risk_level,
        "adverse_media_flag": bool(assessment.adverse_media_flag),
        "beneficial_ownership_confidence_score": assessment.beneficial_ownership_confidence_score,
        "adverse_media_flags": [
            {
                "id": flag.id,
                "source": flag.source,
                "headline": flag.headline,
                "severity": flag.severity,
                "published_date": flag.published_date.isoformat() if flag.published_date else None
            } for flag in adverse_media_flags
        ],
        "beneficial_owners": [
            {
                "owner_name": owner.owner_name,
                "ownership_percentage": owner.ownership_percentage,
                "confidence_score": owner.confidence_score,
                "verification_status": owner.verification_status
            } for owner in beneficial_owners
        ]
    }
    
    return ComplianceStatusResponse(**response_data)
