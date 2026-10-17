import os
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Site(Base):
    __tablename__ = "sites"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=False)
    employee_number = Column(String, unique=True)
    job_title = Column(String)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class MobileCredential(Base):
    __tablename__ = "mobile_credentials"
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    credential_identifier = Column(String, unique=True, nullable=False)
    status = Column(String, nullable=False, default="active")
    issued_at = Column(DateTime, default=func.current_timestamp())
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class CredentialAction(Base):
    __tablename__ = "credential_actions"
    id = Column(Integer, primary_key=True, index=True)
    mobile_credential_id = Column(Integer, ForeignKey("mobile_credentials.id"), nullable=False)
    action_type = Column(String, nullable=False)
    reason = Column(String)
    performed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    performed_at = Column(DateTime, default=func.current_timestamp())
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

# Pydantic models
class EmployeeLookupResponse(BaseModel):
    employee_id: int

class MobileCredentialLookupResponse(BaseModel):
    mobile_credential_id: int

class SuspendCredentialResponse(BaseModel):
    success: bool
    message: str
    credential_id: int

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/lookup-employee-by-name-and-site", response_model=EmployeeLookupResponse, operation_id="lookup_employee_by_name_and_site")
def lookup_employee_by_name_and_site(
    employee_full_name: str = Query(...),
    site_name: str = Query(...),
    db = None
):
    if db is None:
        db = next(get_db())
    
    stmt = select(Employee.id).join(User, Employee.user_id == User.id).join(Site, Employee.site_id == Site.id).where(
        User.full_name == employee_full_name,
        Site.name == site_name
    )
    
    result = db.execute(stmt).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    return EmployeeLookupResponse(employee_id=result[0])

@app.get("/lookup-mobile-credential-by-employee-id", response_model=MobileCredentialLookupResponse, operation_id="lookup_mobile_credential_by_employee_id")
def lookup_mobile_credential_by_employee_id(
    employee_id: int = Query(...),
    db = None
):
    if db is None:
        db = next(get_db())
    
    stmt = select(MobileCredential.id).where(MobileCredential.employee_id == employee_id)
    result = db.execute(stmt).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Mobile credential not found")
    
    return MobileCredentialLookupResponse(mobile_credential_id=result[0])

@app.post("/suspend-mobile-credential", response_model=SuspendCredentialResponse, operation_id="suspend_mobile_credential")
def suspend_mobile_credential(
    employee_name: str = Query(...),
    site_name: str = Query(...),
    reason: str = Query(...),
    db = None
):
    if db is None:
        db = next(get_db())
    
    # Get employee ID
    employee_lookup = lookup_employee_by_name_and_site(employee_full_name=employee_name, site_name=site_name, db=db)
    employee_id = employee_lookup.employee_id
    
    # Get mobile credential ID
    credential_lookup = lookup_mobile_credential_by_employee_id(employee_id=employee_id, db=db)
    credential_id = credential_lookup.mobile_credential_id
    
    # Update credential status
    stmt = text("""
        UPDATE mobile_credentials 
        SET status = 'suspended', updated_at = CURRENT_TIMESTAMP 
        WHERE id = :credential_id
    """)
    db.execute(stmt, {"credential_id": credential_id})
    
    # Record the action
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    action_stmt = text("""
        INSERT INTO credential_actions (mobile_credential_id, action_type, reason, performed_by, performed_at)
        VALUES (:credential_id, 'suspend', :reason, :performed_by, CURRENT_TIMESTAMP)
    """)
    db.execute(action_stmt, {
        "credential_id": credential_id,
        "reason": reason,
        "performed_by": current_user_id
    })
    
    db.commit()
    
    return SuspendCredentialResponse(
        success=True,
        message="Mobile credential suspended successfully",
        credential_id=credential_id
    )
