import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, Time, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    phone = Column(String)
    address = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    license_plate = Column(String)
    mileage = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ServiceAdvisor(Base):
    __tablename__ = "service_advisors"
    
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ServiceType(Base):
    __tablename__ = "service_types"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    estimated_duration_minutes = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Appointment(Base):
    __tablename__ = "appointments"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    service_advisor_id = Column(Integer, ForeignKey("service_advisors.id"), nullable=False)
    service_type_id = Column(Integer, ForeignKey("service_types.id"), nullable=False)
    appointment_date = Column(Date, nullable=False)
    appointment_time = Column(Time, nullable=False)
    status = Column(String, default="scheduled", nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add constraint for status values
    __table_args__ = (
        CheckConstraint("status IN ('scheduled', 'confirmed', 'in_progress', 'completed', 'cancelled', 'no_show')"),
    )

# Pydantic Models
class AppointmentCreate(BaseModel):
    customer_id: int
    vehicle_id: int
    service_advisor_id: int
    service_type_id: int
    appointment_date: str
    appointment_time: str
    notes: Optional[str] = None

class AppointmentResponse(AppointmentCreate):
    id: int
    status: str
    created_at: str
    updated_at: str

class CustomerSearchResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

class VehicleSearchResponse(BaseModel):
    id: int
    customer_id: int
    vin: str
    make: str
    model: str
    year: int
    license_plate: Optional[str] = None
    mileage: Optional[int] = None

class ServiceAdvisorSearchResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None

class ServiceTypeSearchResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    estimated_duration_minutes: Optional[int] = None

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/appointments", response_model=AppointmentResponse, operation_id="create_appointment")
def create_appointment(appointment: AppointmentCreate, db = Depends(get_db)):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Create the appointment
    db_appointment = Appointment(
        customer_id=appointment.customer_id,
        vehicle_id=appointment.vehicle_id,
        service_advisor_id=appointment.service_advisor_id,
        service_type_id=appointment.service_type_id,
        appointment_date=appointment.appointment_date,
        appointment_time=appointment.appointment_time,
        notes=appointment.notes,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(db_appointment)
    db.commit()
    db.refresh(db_appointment)
    
    # Convert datetime fields to strings for response
    return AppointmentResponse(
        id=db_appointment.id,
        customer_id=db_appointment.customer_id,
        vehicle_id=db_appointment.vehicle_id,
        service_advisor_id=db_appointment.service_advisor_id,
        service_type_id=db_appointment.service_type_id,
        appointment_date=str(db_appointment.appointment_date),
        appointment_time=str(db_appointment.appointment_time),
        status=db_appointment.status,
        notes=db_appointment.notes,
        created_at=db_appointment.created_at.isoformat() if db_appointment.created_at else None,
        updated_at=db_appointment.updated_at.isoformat() if db_appointment.updated_at else None
    )

@app.get("/customers/search", response_model=list[CustomerSearchResponse], operation_id="find_customer_by_name")
def find_customer_by_name(first_name: str, last_name: str, db = Depends(get_db)):
    customers = db.query(Customer).filter(
        Customer.first_name == first_name,
        Customer.last_name == last_name
    ).all()
    
    return [
        CustomerSearchResponse(
            id=c.id,
            first_name=c.first_name,
            last_name=c.last_name,
            email=c.email,
            phone=c.phone,
            address=c.address
        ) for c in customers
    ]

@app.get("/vehicles/search", response_model=list[VehicleSearchResponse], operation_id="find_vehicle_by_vin")
def find_vehicle_by_vin(vin: str, db = Depends(get_db)):
    vehicles = db.query(Vehicle).filter(Vehicle.vin == vin).all()
    
    return [
        VehicleSearchResponse(
            id=v.id,
            customer_id=v.customer_id,
            vin=v.vin,
            make=v.make,
            model=v.model,
            year=v.year,
            license_plate=v.license_plate,
            mileage=v.mileage
        ) for v in vehicles
    ]

@app.get("/service_advisors/search", response_model=list[ServiceAdvisorSearchResponse], operation_id="find_service_advisor_by_name")
def find_service_advisor_by_name(first_name: str, last_name: str, db = Depends(get_db)):
    advisors = db.query(ServiceAdvisor).filter(
        ServiceAdvisor.first_name == first_name,
        ServiceAdvisor.last_name == last_name
    ).all()
    
    return [
        ServiceAdvisorSearchResponse(
            id=a.id,
            first_name=a.first_name,
            last_name=a.last_name,
            email=a.email,
            phone=a.phone
        ) for a in advisors
    ]

@app.get("/service_types/search", response_model=list[ServiceTypeSearchResponse], operation_id="find_service_type_by_name")
def find_service_type_by_name(name: str, db = Depends(get_db)):
    service_types = db.query(ServiceType).filter(ServiceType.name == name).all()
    
    return [
        ServiceTypeSearchResponse(
            id=s.id,
            name=s.name,
            description=s.description,
            estimated_duration_minutes=s.estimated_duration_minutes
        ) for s in service_types
    ]
