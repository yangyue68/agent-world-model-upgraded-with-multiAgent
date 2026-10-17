import os
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Float, ForeignKey, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, unique=True)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Stakeholder(Base):
    __tablename__ = "stakeholders"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False)
    email = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Security(Base):
    __tablename__ = "securities"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    stakeholder_id = Column(Integer, ForeignKey("stakeholders.id"), nullable=False)
    security_type = Column(String, nullable=False)
    certificate_number = Column(String)
    quantity = Column(Float, nullable=False, default=0)
    price_per_unit = Column(Float)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Valuation(Base):
    __tablename__ = "valuations"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    valuation_date = Column(String, nullable=False)
    pre_money_valuation = Column(Float)
    post_money_valuation = Column(Float)
    share_price = Column(Float)
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action_type = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer)
    stakeholder_id = Column(Integer, ForeignKey("stakeholders.id"))
    security_id = Column(Integer, ForeignKey("securities.id"))
    previous_value = Column(Text)
    new_value = Column(Text)
    description = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# Pydantic Models
class ActingUser(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None

class AffectedStakeholder(BaseModel):
    id: int
    name: str

class AffectedSecurity(BaseModel):
    id: int
    security_type: str
    quantity: float
    price_per_unit: float

class AuditHistoryEntry(BaseModel):
    id: int
    action_type: str
    entity_type: str
    entity_id: Optional[int] = None
    stakeholder_id: Optional[int] = None
    security_id: Optional[int] = None
    previous_value: Optional[str] = None
    new_value: Optional[str] = None
    description: Optional[str] = None
    created_at: str
    acting_user: ActingUser
    affected_stakeholder: Optional[AffectedStakeholder] = None
    affected_security: Optional[AffectedSecurity] = None

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/companies/{company_id}/audit-history", response_model=List[AuditHistoryEntry], operation_id="get_company_audit_history")
def get_company_audit_history(
    company_id: int,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db = Depends(get_db)
):
    # Set default dates if not provided
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    else:
        end_date = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    
    if start_date is None:
        start_date_obj = datetime.now() - timedelta(days=365)
        start_date = start_date_obj.strftime("%Y-%m-%d")
    else:
        start_date = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    
    # Query audit logs for the company within the date range
    audit_logs = db.query(AuditLog)\
        .filter(
            AuditLog.company_id == company_id,
            AuditLog.created_at >= start_date,
            AuditLog.created_at <= end_date
        )\
        .order_by(AuditLog.created_at.desc())\
        .all()
    
    # Process audit logs into response format
    result = []
    for log in audit_logs:
        # Get acting user
        acting_user = db.query(User).filter(User.id == log.user_id).first()
        
        # Get affected stakeholder if exists
        affected_stakeholder = None
        if log.stakeholder_id:
            stakeholder = db.query(Stakeholder).filter(Stakeholder.id == log.stakeholder_id).first()
            if stakeholder:
                affected_stakeholder = AffectedStakeholder(
                    id=stakeholder.id,
                    name=stakeholder.name
                )
        
        # Get affected security if exists
        affected_security = None
        if log.security_id:
            security = db.query(Security).filter(Security.id == log.security_id).first()
            if security:
                affected_security = AffectedSecurity(
                    id=security.id,
                    security_type=security.security_type,
                    quantity=security.quantity,
                    price_per_unit=security.price_per_unit or 0.0
                )
        
        entry = AuditHistoryEntry(
            id=log.id,
            action_type=log.action_type,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            stakeholder_id=log.stakeholder_id,
            security_id=log.security_id,
            previous_value=log.previous_value,
            new_value=log.new_value,
            description=log.description,
            created_at=log.created_at.isoformat(),
            acting_user=ActingUser(
                id=acting_user.id,
                username=acting_user.username,
                full_name=acting_user.full_name
            ),
            affected_stakeholder=affected_stakeholder,
            affected_security=affected_security
        )
        result.append(entry)
    
    return result
