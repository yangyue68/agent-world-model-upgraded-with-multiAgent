import os
from datetime import datetime, date
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    Date,
    DateTime,
    ForeignKey,
    inspect,
    text,
)
from sqlalchemy.orm import sessionmaker, declarative_base
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- SQLAlchemy Models for Schema Reflection/Usage ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Candidate(Base):
    __tablename__ = "candidates"
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Certification(Base):
    __tablename__ = "certifications"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Badge(Base):
    __tablename__ = "badges"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False)
    certification_id = Column(Integer, ForeignKey("certifications.id"), nullable=False)
    certification_identifier = Column(String, unique=True, nullable=False)
    issued_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    issued_at = Column(DateTime, default=datetime.utcnow)
    expiration_date = Column(Date)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# --- Pydantic Schemas ---

class BadgeCreateRequest(BaseModel):
    candidate_name: str
    certification_name: str
    certification_identifier: str
    expiration_date: str  # YYYY-MM-DD

class BadgeResponse(BaseModel):
    badge_id: int
    candidate_name: str
    certification_name: str
    certification_identifier: str
    expiration_date: str
    issued_at: str

    class Config:
        from_attributes = True

# --- App Setup ---

app = FastAPI(title="CertChain API", version="1.0.0")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/badges", response_model=BadgeResponse, operation_id="issue_badge", tags=["PrimaryResource"])
def issue_badge(request: BadgeCreateRequest):
    db = SessionLocal()
    try:
        # 1. Find or Create Candidate
        candidate = db.query(Candidate).filter(Candidate.full_name == request.candidate_name).first()
        if not candidate:
            candidate = Candidate(full_name=request.candidate_name)
            db.add(candidate)
            db.flush()

        # 2. Find or Create Certification
        certification = db.query(Certification).filter(Certification.name == request.certification_name).first()
        if not certification:
            certification = Certification(name=request.certification_name)
            db.add(certification)
            db.flush()

        # 3. Get Current User ID from Environment
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id_str:
            raise HTTPException(status_code=500, detail="Internal Server Error: User context missing")
        
        try:
            current_user_id = int(current_user_id_str)
        except ValueError:
            raise HTTPException(status_code=500, detail="Internal Server Error: Invalid User ID format")

        # Validate user exists
        user = db.query(User).filter(User.id == current_user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Issuing user not found")

        # 4. Parse Expiration Date
        try:
            exp_date = date.fromisoformat(request.expiration_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid expiration date format. Use YYYY-MM-DD.")

        # 5. Check for existing badge with same identifier to avoid duplicates if needed, 
        # though spec doesn't explicitly forbid it, uniqueness constraint on cert_identifier handles DB side.
        existing_badge = db.query(Badge).filter(Badge.certification_identifier == request.certification_identifier).first()
        if existing_badge:
            raise HTTPException(status_code=409, detail=f"Badge with identifier {request.certification_identifier} already exists")

        # 6. Create Badge
        now = datetime.utcnow()
        new_badge = Badge(
            candidate_id=candidate.id,
            certification_id=certification.id,
            certification_identifier=request.certification_identifier,
            issued_by_user_id=current_user_id,
            issued_at=now,
            expiration_date=exp_date,
            status="active"
        )
        db.add(new_badge)
        db.commit()
        db.refresh(new_badge)

        # 7. Construct Response
        return BadgeResponse(
            badge_id=new_badge.id,
            candidate_name=candidate.full_name,
            certification_name=certification.name,
            certification_identifier=new_badge.certification_identifier,
            expiration_date=new_badge.expiration_date.isoformat(),
            issued_at=new_badge.issued_at.isoformat()
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
