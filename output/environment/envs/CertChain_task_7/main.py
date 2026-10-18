import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import Session, declarative_base

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, pool_pre_ping=True)
Base = declarative_base()


# --- SQLAlchemy Models for Reading ---

class Badge(Base):
    __tablename__ = "badges"
    id = Column(Integer, primary_key=True)
    badge_id = Column(Text, unique=True, nullable=False)
    user_id = Column(Integer, nullable=False)
    issuer_id = Column(Integer, nullable=False)
    title = Column(Text, nullable=False)
    description = Column(Text)
    issued_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime)
    status = Column(Text, nullable=False, default="valid")
    metadata_json = Column('metadata', Text)  # Use column name 'metadata' but attribute 'metadata_json'
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class Issuer(Base):
    __tablename__ = "issuers"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    url = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(Text, unique=True, nullable=False)
    display_name = Column(Text)
    email = Column(Text, unique=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


# --- Pydantic Schemas ---

class BadgeDetailResponse(BaseModel):
    id: int
    badge_id: str
    user_id: int
    issuer_id: int
    title: str
    description: str
    issued_at: str
    expires_at: Optional[str] = None
    status: str
    metadata: str
    created_at: str
    updated_at: str


class VerifyRequest(BaseModel):
    verified_by: int = Field(..., description="ID of the user performing the verification")


class VerifyResponse(BaseModel):
    badge_id: str
    result: str  # "valid" or "invalid"
    details: str
    verified_at: str


# --- App Setup ---

app = FastAPI(title="CertChain API", version="1.0.0")


def get_db():
    with Session(engine) as session:
        yield session


@app.get("/badges/{badge_id}", operation_id="get_badge_details", tags=["PrimaryResource"])
def get_badge_details(badge_id: str = Path(..., description="Unique identifier of the digital badge")):
    """Retrieve detailed information about a specific digital badge."""
    stmt = select(Badge).where(Badge.badge_id == badge_id)
    
    with Session(engine) as db:
        badge = db.execute(stmt).scalar_one_or_none()
        
    if not badge:
        raise HTTPException(status_code=404, detail=f"Badge with ID {badge_id} not found.")
    
    return {
        "id": badge.id,
        "badge_id": badge.badge_id,
        "user_id": badge.user_id,
        "issuer_id": badge.issuer_id,
        "title": badge.title,
        "description": badge.description or "",
        "issued_at": badge.issued_at.isoformat() if isinstance(badge.issued_at, datetime) else badge.issued_at,
        "expires_at": badge.expires_at.isoformat() if badge.expires_at and isinstance(badge.expires_at, datetime) else None,
        "status": badge.status,
        "metadata": badge.metadata_json or "",
        "created_at": badge.created_at.isoformat() if isinstance(badge.created_at, datetime) else badge.created_at,
        "updated_at": badge.updated_at.isoformat() if isinstance(badge.updated_at, datetime) else badge.updated_at,
    }


@app.post("/badges/{badge_id}/verify", operation_id="verify_badge", tags=["PrimaryResource"])
def verify_badge(
    badge_id: str = Path(..., description="Unique identifier of the digital badge to verify"),
    body: VerifyRequest = None,
):
    """Verify the authenticity of a digital badge by checking its metadata against the CertChain database."""
    
    verified_by_user_id = body.verified_by
    
    # Check if user exists
    user_stmt = select(User).where(User.id == verified_by_user_id)
    with Session(engine) as db:
        user = db.execute(user_stmt).scalar_one_or_none()
        
    if not user:
        raise HTTPException(status_code=404, detail=f"User with ID {verified_by_user_id} not found.")

    # Fetch badge
    badge_stmt = select(Badge).where(Badge.badge_id == badge_id)
    with Session(engine) as db:
        badge = db.execute(badge_stmt).scalar_one_or_none()
        
    if not badge:
        raise HTTPException(status_code=404, detail=f"Badge with ID {badge_id} not found.")

    # Determine validity
    current_time = datetime.now(timezone.utc).replace(tzinfo=None)
    
    is_expired = False
    if badge.expires_at:
        expires_at_dt = badge.expires_at
        if isinstance(expires_at_dt, str):
            try:
                expires_at_dt = datetime.fromisoformat(expires_at_dt.replace('Z', '+00:00')).replace(tzinfo=None)
            except ValueError:
                pass
        
        if current_time > expires_at_dt:
            is_expired = True

    if badge.status == 'valid' and not is_expired:
        result = "valid"
        details = f"Badge {badge_id} is active and metadata matches issuer records."
    else:
        result = "invalid"
        reasons = []
        if badge.status != 'valid':
            reasons.append(f"Status is '{badge.status}'")
        if is_expired:
            reasons.append("Badge has expired")
        details = f"Verification failed: {'; '.join(reasons)}."

    # Log the verification
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    log_stmt = text("""
        INSERT INTO verification_logs (badge_id, verified_by, verified_at, result, details)
        VALUES (:badge_id, :verified_by, :verified_at, :result, :details)
    """)
    
    with Session(engine) as db:
        db.execute(log_stmt, {
            "badge_id": badge_id,
            "verified_by": verified_by_user_id,
            "verified_at": now_str,
            "result": result,
            "details": details
        })
        db.commit()

    return {
        "badge_id": badge_id,
        "result": result,
        "details": details,
        "verified_at": now_str
    }
