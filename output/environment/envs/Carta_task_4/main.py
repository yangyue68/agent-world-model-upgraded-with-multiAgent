import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, ForeignKey, CheckConstraint, func, case
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

class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    legal_name = Column(String)
    incorporation_state = Column(String)
    incorporation_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Stakeholder(Base):
    __tablename__ = "stakeholders"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String, nullable=False)
    stakeholder_type = Column(String, nullable=False)
    email = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add constraint for stakeholder_type
    __table_args__ = (
        CheckConstraint("stakeholder_type IN ('founder', 'employee', 'investor')"),
    )

class ShareClass(Base):
    __tablename__ = "share_classes"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String, nullable=False)
    class_type = Column(String, nullable=False)
    authorized_shares = Column(Integer, default=0)
    par_value = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add constraint for class_type
    __table_args__ = (
        CheckConstraint("class_type IN ('common', 'preferred')"),
    )

class ShareIssuance(Base):
    __tablename__ = "share_issuances"
    id = Column(Integer, primary_key=True, index=True)
    stakeholder_id = Column(Integer, ForeignKey("stakeholders.id"), nullable=False)
    share_class_id = Column(Integer, ForeignKey("share_classes.id"), nullable=False)
    shares = Column(Integer, nullable=False)
    issuance_date = Column(Date, nullable=False)
    certificate_number = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add constraint for shares
    __table_args__ = (
        CheckConstraint("shares > 0"),
    )

class OptionGrant(Base):
    __tablename__ = "option_grants"
    id = Column(Integer, primary_key=True, index=True)
    stakeholder_id = Column(Integer, ForeignKey("stakeholders.id"), nullable=False)
    share_class_id = Column(Integer, ForeignKey("share_classes.id"), nullable=False)
    shares = Column(Integer, nullable=False)
    grant_date = Column(Date, nullable=False)
    vesting_start_date = Column(Date)
    vesting_schedule = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add constraint for shares
    __table_args__ = (
        CheckConstraint("shares > 0"),
    )

class OptionPool(Base):
    __tablename__ = "option_pools"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    share_class_id = Column(Integer, ForeignKey("share_classes.id"), nullable=False)
    total_reserved = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ConvertibleInstrument(Base):
    __tablename__ = "convertible_instruments"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    stakeholder_id = Column(Integer, ForeignKey("stakeholders.id"), nullable=False)
    instrument_type = Column(String, nullable=False)
    principal_amount = Column(Integer, nullable=False)
    valuation_cap = Column(Integer)
    discount_rate = Column(Integer)
    conversion_shares = Column(Integer)
    issue_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add constraint for instrument_type
    __table_args__ = (
        CheckConstraint("instrument_type IN ('safe', 'convertible_note')"),
    )

# Pydantic Models
class StakeholderOut(BaseModel):
    id: int
    name: str
    stakeholder_type: str
    shares_owned: int
    percentage: float

class TotalsOut(BaseModel):
    outstanding_shares: int
    reserved_shares: int
    fully_diluted_shares: int

class CapTableOut(BaseModel):
    company_name: str
    stakeholders: List[StakeholderOut]
    totals: TotalsOut

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/companies/{company_id}/cap_table", response_model=CapTableOut, operation_id="get_cap_table")
def get_cap_table(company_id: int):
    db = next(get_db())
    
    # Get company info
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    # Calculate total outstanding shares
    total_outstanding = db.query(func.sum(ShareIssuance.shares)).join(
        Stakeholder, ShareIssuance.stakeholder_id == Stakeholder.id
    ).filter(Stakeholder.company_id == company_id).scalar() or 0
    
    # Calculate total reserved shares
    total_reserved = db.query(func.sum(OptionPool.total_reserved)).filter(
        OptionPool.company_id == company_id
    ).scalar() or 0
    
    # Calculate fully diluted shares
    fully_diluted = total_outstanding + total_reserved
    
    # Get all stakeholders for this company
    stakeholders = db.query(Stakeholder).filter(Stakeholder.company_id == company_id).all()
    
    # Prepare stakeholder data
    stakeholder_data = []
    
    for stakeholder in stakeholders:
        # Calculate owned shares for this stakeholder
        owned_shares = 0
        
        # Add shares from share issuances
        issuance_query = db.query(func.sum(ShareIssuance.shares)).filter(
            ShareIssuance.stakeholder_id == stakeholder.id
        )
        issuance_shares = issuance_query.scalar() or 0
        owned_shares += issuance_shares
        
        # Add shares from option grants (assuming they're all vested)
        option_query = db.query(func.sum(OptionGrant.shares)).filter(
            OptionGrant.stakeholder_id == stakeholder.id
        )
        option_shares = option_query.scalar() or 0
        owned_shares += option_shares
        
        # Add shares from convertible instruments
        convertible_query = db.query(func.sum(ConvertibleInstrument.conversion_shares)).filter(
            ConvertibleInstrument.stakeholder_id == stakeholder.id
        )
        convertible_shares = convertible_query.scalar() or 0
        owned_shares += convertible_shares
        
        # Calculate percentage
        percentage = 0.0
        if fully_diluted > 0:
            percentage = (owned_shares / fully_diluted) * 100
            
        stakeholder_data.append({
            "id": stakeholder.id,
            "name": stakeholder.name,
            "stakeholder_type": stakeholder.stakeholder_type,
            "shares_owned": owned_shares,
            "percentage": round(percentage, 2)
        })
    
    return {
        "company_name": company.name,
        "stakeholders": stakeholder_data,
        "totals": {
            "outstanding_shares": total_outstanding,
            "reserved_shares": total_reserved,
            "fully_diluted_shares": fully_diluted
        }
    }
