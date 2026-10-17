import os
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Path
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Float, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Company(Base):
    __tablename__ = "companies"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Stakeholder(Base):
    __tablename__ = "stakeholders"
    
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    full_name = Column(String, nullable=False)
    title = Column(String)
    employment_status = Column(String, CheckConstraint("employment_status IN ('Active', 'Inactive', 'Terminated', 'On Leave')"))
    email = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    company = relationship("Company")
    user = relationship("User")

class Security(Base):
    __tablename__ = "securities"
    
    id = Column(Integer, primary_key=True, index=True)
    stakeholder_id = Column(Integer, ForeignKey("stakeholders.id"), nullable=False)
    security_type = Column(String, CheckConstraint("security_type IN ('Common Stock', 'Preferred Stock', 'Option', 'Warrant', 'Convertible Note', 'SAFE')"))
    shares = Column(Float, default=0.0)
    ownership_percentage = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    stakeholder = relationship("Stakeholder")

app = FastAPI()

class UpdateStakeholderRequest(BaseModel):
    title: Optional[str] = None
    employment_status: Optional[Literal["Active", "Inactive", "Terminated", "On Leave"]] = None
    email: Optional[str] = None

class StakeholderResponse(BaseModel):
    id: int
    company_id: int
    user_id: Optional[int]
    full_name: str
    title: str
    employment_status: str
    email: str
    created_at: datetime
    updated_at: datetime

@app.put("/stakeholders/{stakeholder_id}", response_model=StakeholderResponse, operation_id="update_stakeholder_record")
def update_stakeholder_record(
    stakeholder_id: int = Path(..., description="Internal ID of the stakeholder record to update"),
    update_data: UpdateStakeholderRequest = None
):
    db = SessionLocal()
    try:
        stakeholder = db.query(Stakeholder).filter(Stakeholder.id == stakeholder_id).first()
        if not stakeholder:
            raise HTTPException(status_code=404, detail="Stakeholder not found")
        
        # Update fields if provided
        if update_data.title is not None:
            stakeholder.title = update_data.title
        if update_data.employment_status is not None:
            stakeholder.employment_status = update_data.employment_status
        if update_data.email is not None:
            stakeholder.email = update_data.email
            
        stakeholder.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(stakeholder)
        
        return StakeholderResponse(
            id=stakeholder.id,
            company_id=stakeholder.company_id,
            user_id=stakeholder.user_id,
            full_name=stakeholder.full_name,
            title=stakeholder.title,
            employment_status=stakeholder.employment_status,
            email=stakeholder.email,
            created_at=stakeholder.created_at,
            updated_at=stakeholder.updated_at
        )
    finally:
        db.close()
