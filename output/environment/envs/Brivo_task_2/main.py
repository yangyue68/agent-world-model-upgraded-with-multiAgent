from fastapi import FastAPI, HTTPException, Path, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime, date

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
    phone = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Contractor(Base):
    __tablename__ = "contractors"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, nullable=False)
    company_name = Column(String)
    contact_name = Column(String)
    phone = Column(String)
    email = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class MobileCredential(Base):
    __tablename__ = "mobile_credentials"
    id = Column(Integer, primary_key=True, index=True)
    contractor_id = Column(Integer, ForeignKey("contractors.id"), nullable=False)
    credential_number = Column(String, unique=True, nullable=False)
    device_identifier = Column(String, nullable=False)
    status = Column(String, nullable=False, default="active")
    expiration_date = Column(Date)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

# Pydantic Models
class MobileCredentialResponse(BaseModel):
    id: int
    contractor_id: int
    credential_number: str
    device_identifier: str
    status: str
    expiration_date: str
    created_at: str
    updated_at: str

class MobileCredentialUpdate(BaseModel):
    device_identifier: str
    expiration_date: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/mobile-credentials/{credential_number}", response_model=MobileCredentialResponse, operation_id="update_mobile_credential")
def update_mobile_credential(
    credential_number: str = Path(..., description="The credential number of the mobile credential to update"),
    credential_update: MobileCredentialUpdate = None,
    db = Depends(get_db)
):
    # Get current user from environment
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    
    # Find the mobile credential by credential_number
    db_credential = db.query(MobileCredential).filter(MobileCredential.credential_number == credential_number).first()
    
    if not db_credential:
        raise HTTPException(status_code=404, detail="Mobile credential not found")
    
    # Update fields if provided
    if credential_update.device_identifier is not None:
        db_credential.device_identifier = credential_update.device_identifier
    
    if credential_update.expiration_date is not None:
        # Parse the expiration date string to a date object
        db_credential.expiration_date = date.fromisoformat(credential_update.expiration_date)
    
    # Keep status active
    db_credential.status = "active"
    
    # Update timestamp
    db_credential.updated_at = func.current_timestamp()
    
    # Commit changes
    db.commit()
    db.refresh(db_credential)
    
    # Convert dates to strings for response
    return {
        "id": db_credential.id,
        "contractor_id": db_credential.contractor_id,
        "credential_number": db_credential.credential_number,
        "device_identifier": db_credential.device_identifier,
        "status": db_credential.status,
        "expiration_date": db_credential.expiration_date.isoformat() if db_credential.expiration_date else None,
        "created_at": db_credential.created_at.isoformat() if db_credential.created_at else None,
        "updated_at": db_credential.updated_at.isoformat() if db_credential.updated_at else None
    }
