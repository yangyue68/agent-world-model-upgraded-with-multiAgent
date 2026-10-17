import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Text, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
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
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Shop(Base):
    __tablename__ = "shops"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    zip = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ServicePackage(Base):
    __tablename__ = "service_packages"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    labor_rate = Column(Float, nullable=False)
    estimated_duration_minutes = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("labor_rate >= 0"), CheckConstraint("estimated_duration_minutes > 0"),)

class Part(Base):
    __tablename__ = "parts"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    unit_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("unit_price >= 0"),)

class ServicePackagePart(Base):
    __tablename__ = "service_package_parts"
    id = Column(Integer, primary_key=True)
    service_package_id = Column(Integer, ForeignKey("service_packages.id"))
    part_id = Column(Integer, ForeignKey("parts.id"))
    quantity = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("quantity > 0"),)

class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    appointment_code = Column(String, unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    shop_id = Column(Integer, ForeignKey("shops.id"))
    service_package_id = Column(Integer, ForeignKey("service_packages.id"))
    appointment_datetime = Column(DateTime, nullable=False)
    status = Column(String, default="scheduled")
    pre_authorized_estimate = Column(Float, nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("pre_authorized_estimate >= 0"),)

class AppointmentChangeLog(Base):
    __tablename__ = "appointment_change_log"
    id = Column(Integer, primary_key=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"))
    changed_by_user_id = Column(Integer, ForeignKey("users.id"))
    old_service_package_id = Column(Integer)
    new_service_package_id = Column(Integer)
    old_estimate = Column(Float)
    new_estimate = Column(Float)
    change_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class AppointmentResponse(BaseModel):
    id: int
    appointment_code: str
    user_id: int
    shop_id: int
    service_package_id: int
    appointment_datetime: datetime
    status: str
    pre_authorized_estimate: float
    notes: str
    created_at: datetime
    updated_at: datetime

class UpdateServicePackageRequest(BaseModel):
    new_service_package_id: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/appointments/{appointment_id}", response_model=AppointmentResponse, operation_id="update_appointment_service_package")
def update_appointment_service_package(
    appointment_id: str,
    request: UpdateServicePackageRequest,
    db = None
):
    if db is None:
        db = next(get_db())
    
    # Get current appointment
    appointment = db.query(Appointment).filter(Appointment.appointment_code == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    
    # Get new service package
    new_service_package = db.query(ServicePackage).filter(ServicePackage.id == request.new_service_package_id).first()
    if not new_service_package:
        raise HTTPException(status_code=404, detail="New service package not found")
    
    # Calculate new estimate
    # Labor cost
    labor_cost = new_service_package.labor_rate
    
    # Parts cost
    parts_cost = db.query(ServicePackagePart).join(Part).filter(
        ServicePackagePart.service_package_id == request.new_service_package_id
    ).with_entities(
        case((ServicePackagePart.quantity == 1, Part.unit_price),
             else_=ServicePackagePart.quantity * Part.unit_price).label('total')
    ).scalar_subquery()
    
    # Total estimate
    total_estimate = labor_cost + parts_cost if parts_cost else labor_cost
    
    # Create change log entry
    change_log_entry = AppointmentChangeLog(
        appointment_id=appointment.id,
        changed_by_user_id=int(os.environ.get("AWM_CURRENT_USER_ID")),
        old_service_package_id=appointment.service_package_id,
        new_service_package_id=request.new_service_package_id,
        old_estimate=appointment.pre_authorized_estimate,
        new_estimate=total_estimate,
        change_reason="Customer upgraded to full synthetic package."
    )
    db.add(change_log_entry)
    
    # Update appointment
    appointment.service_package_id = request.new_service_package_id
    appointment.pre_authorized_estimate = total_estimate
    appointment.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(appointment)
    
    return appointment
