import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, Float, CheckConstraint, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Constituent(Base):
    __tablename__ = "constituents"
    
    id = Column(String, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    address = Column(Text)
    is_deleted = Column(Integer, nullable=False, default=0, server_default="0")
    deleted_at = Column(DateTime)
    deleted_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class CommunicationHistory(Base):
    __tablename__ = "communication_history"
    
    id = Column(Integer, primary_key=True)
    constituent_id = Column(String, ForeignKey("constituents.id"))
    communication_type = Column(String, nullable=False)
    subject = Column(String)
    content = Column(Text)
    communication_date = Column(DateTime, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class GiftRecord(Base):
    __tablename__ = "gift_records"
    
    id = Column(Integer, primary_key=True)
    constituent_id = Column(String, ForeignKey("constituents.id"))
    amount = Column(Float, nullable=False)
    gift_date = Column(DateTime, nullable=False)
    gift_type = Column(String)
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class ConstituentResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: Optional[str]
    phone: Optional[str]
    address: Optional[str]
    is_deleted: int
    deleted_at: Optional[str]
    deleted_by: Optional[int]
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

@app.put("/constituents/{constituent_id}/soft-delete", response_model=ConstituentResponse, operation_id="soft_delete_constituent")
async def soft_delete_constituent(constituent_id: str, db: Session = Depends(get_db)):
    # Get the current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find the constituent
    constituent = db.query(Constituent).filter(Constituent.id == constituent_id).first()
    
    if not constituent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Constituent not found")
    
    # Soft delete the constituent
    constituent.is_deleted = 1
    constituent.deleted_at = datetime.utcnow()
    constituent.deleted_by = current_user_id
    
    db.commit()
    db.refresh(constituent)
    
    # Convert datetime fields to ISO format strings
    return {
        "id": constituent.id,
        "first_name": constituent.first_name,
        "last_name": constituent.last_name,
        "email": constituent.email,
        "phone": constituent.phone,
        "address": constituent.address,
        "is_deleted": constituent.is_deleted,
        "deleted_at": constituent.deleted_at.isoformat() if constituent.deleted_at else None,
        "deleted_by": constituent.deleted_by,
        "created_at": constituent.created_at.isoformat(),
        "updated_at": constituent.updated_at.isoformat()
    }
