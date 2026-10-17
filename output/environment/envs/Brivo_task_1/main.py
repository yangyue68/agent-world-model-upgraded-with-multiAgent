import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class Site(Base):
    __tablename__ = "sites"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    address = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    full_name = Column(String, nullable=False)
    email = Column(String)
    site_id = Column(Integer, ForeignKey("sites.id"))
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())
    user = relationship("User")
    site = relationship("Site")

class BadgeCredential(Base):
    __tablename__ = "badge_credentials"
    id = Column(Integer, primary_key=True)
    badge_number = Column(String, unique=True, nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    status = Column(String, nullable=False, default="active")
    suspended_at = Column(DateTime)
    suspension_reason = Column(Text)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())
    employee = relationship("Employee")

class BadgeCredentialEvent(Base):
    __tablename__ = "badge_credential_events"
    id = Column(Integer, primary_key=True)
    badge_credential_id = Column(Integer, ForeignKey("badge_credentials.id"), nullable=False)
    event_type = Column(String, nullable=False)
    reason = Column(Text)
    performed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    badge_credential = relationship("BadgeCredential")
    performed_by_user = relationship("User")

app = FastAPI()

class SuspendBadgeRequest(BaseModel):
    reason: str

class BadgeCredentialResponse(BaseModel):
    id: int
    badge_number: str
    status: str
    suspended_at: Optional[str]
    suspension_reason: Optional[str]

@app.put("/badge_credentials/{badge_number}/suspend", response_model=BadgeCredentialResponse, operation_id="suspend_badge_credential")
def suspend_badge_credential(
    badge_number: str = Path(..., description="The badge number of the credential to suspend"),
    request: SuspendBadgeRequest = None
):
    db = SessionLocal()
    try:
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
        
        badge_credential = db.query(BadgeCredential).filter(BadgeCredential.badge_number == badge_number).first()
        if not badge_credential:
            raise HTTPException(status_code=404, detail="Badge credential not found")
        
        if badge_credential.status == "suspended":
            raise HTTPException(status_code=400, detail="Badge credential is already suspended")
        
        # Update badge credential
        badge_credential.status = "suspended"
        badge_credential.suspended_at = datetime.utcnow()
        badge_credential.suspension_reason = request.reason
        
        # Create event
        event = BadgeCredentialEvent(
            badge_credential_id=badge_credential.id,
            event_type="suspended",
            reason=request.reason,
            performed_by_user_id=current_user_id
        )
        db.add(event)
        
        db.commit()
        db.refresh(badge_credential)
        
        return BadgeCredentialResponse(
            id=badge_credential.id,
            badge_number=badge_credential.badge_number,
            status=badge_credential.status,
            suspended_at=badge_credential.suspended_at.isoformat() if badge_credential.suspended_at else None,
            suspension_reason=badge_credential.suspension_reason
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
