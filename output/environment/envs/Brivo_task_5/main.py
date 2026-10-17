import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Office(Base):
    __tablename__ = "offices"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    country = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Visitor(Base):
    __tablename__ = "visitors"
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    company = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class VisitorInvitation(Base):
    __tablename__ = "visitor_invitations"
    id = Column(Integer, primary_key=True)
    visitor_id = Column(Integer, ForeignKey("visitors.id"), nullable=False)
    host_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    office_id = Column(Integer, ForeignKey("offices.id"), nullable=False)
    scheduled_start = Column(DateTime, nullable=False)
    scheduled_end = Column(DateTime, nullable=False)
    status = Column(String, default="scheduled", nullable=False)
    cancellation_note = Column(Text)
    cancelled_at = Column(DateTime)
    cancelled_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('scheduled', 'cancelled', 'completed', 'no_show')"),
    )

class ExpectedVisit(Base):
    __tablename__ = "expected_visits"
    id = Column(Integer, primary_key=True)
    visitor_invitation_id = Column(Integer, ForeignKey("visitor_invitations.id"), unique=True, nullable=False)
    expected_arrival = Column(DateTime, nullable=False)
    expected_departure = Column(DateTime, nullable=False)
    status = Column(String, default="expected", nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('expected', 'cancelled', 'arrived', 'completed', 'no_show')"),
    )

# Request and response models
class CancelVisitorInvitationRequest(BaseModel):
    cancellation_note: Optional[str] = None

class VisitorInvitationResponse(BaseModel):
    id: int
    status: str
    cancellation_note: Optional[str]
    cancelled_at: Optional[datetime]
    cancelled_by_user_id: Optional[int]

class ExpectedVisitResponse(BaseModel):
    id: int
    status: str

class CancelVisitorInvitationResponse(BaseModel):
    visitor_invitation: VisitorInvitationResponse
    expected_visit: ExpectedVisitResponse

# Create app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/visitor_invitations/{id}/cancel", response_model=CancelVisitorInvitationResponse, operation_id="cancel_visitor_invitation")
def cancel_visitor_invitation(
    id: int,
    request: CancelVisitorInvitationRequest,
    db: Session = Depends(get_db)
):
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find the visitor invitation
    visitor_invitation = db.query(VisitorInvitation).filter(VisitorInvitation.id == id).first()
    if not visitor_invitation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visitor invitation not found")
    
    # Find the associated expected visit
    expected_visit = db.query(ExpectedVisit).filter(ExpectedVisit.visitor_invitation_id == id).first()
    if not expected_visit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expected visit not found")
    
    # Update visitor invitation
    visitor_invitation.status = "cancelled"
    visitor_invitation.cancellation_note = request.cancellation_note or "Meeting postponed by host"
    visitor_invitation.cancelled_at = datetime.utcnow()
    visitor_invitation.cancelled_by_user_id = current_user_id
    
    # Update expected visit
    expected_visit.status = "cancelled"
    
    # Commit changes
    db.commit()
    db.refresh(visitor_invitation)
    db.refresh(expected_visit)
    
    # Prepare response
    return {
        "visitor_invitation": {
            "id": visitor_invitation.id,
            "status": visitor_invitation.status,
            "cancellation_note": visitor_invitation.cancellation_note,
            "cancelled_at": visitor_invitation.cancelled_at,
            "cancelled_by_user_id": visitor_invitation.cancelled_by_user_id
        },
        "expected_visit": {
            "id": expected_visit.id,
            "status": expected_visit.status
        }
    }
