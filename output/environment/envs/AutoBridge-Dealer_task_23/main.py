import os
from datetime import datetime
from typing import Optional, Generator
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

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
    phone = Column(String)
    email = Column(String)
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Technician(Base):
    __tablename__ = "technicians"
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)
    specialty = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ServiceBay(Base):
    __tablename__ = "service_bays"
    id = Column(Integer, primary_key=True, index=True)
    bay_number = Column(Integer, unique=True, nullable=False)
    description = Column(Text)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class RepairOrder(Base):
    __tablename__ = "repair_orders"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    technician_id = Column(Integer, ForeignKey("technicians.id"))
    service_bay_id = Column(Integer, ForeignKey("service_bays.id"))
    status = Column(String, nullable=False, default="Pending")
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class RepairOrderItem(Base):
    __tablename__ = "repair_order_items"
    id = Column(Integer, primary_key=True, index=True)
    repair_order_id = Column(Integer, ForeignKey("repair_orders.id"), nullable=False)
    description = Column(Text, nullable=False)
    item_type = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    first_name = Column(String)
    last_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class RepairOrderCreate(BaseModel):
    job_description: str

class RepairOrderResponse(BaseModel):
    id: int
    customer_id: int
    vehicle_id: int
    technician_id: Optional[int]
    service_bay_id: Optional[int]
    status: str
    notes: Optional[str]
    created_by: int
    created_at: datetime
    updated_at: datetime

# FastAPI App
app = FastAPI()

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/repair-orders", response_model=RepairOrderResponse, operation_id="create_repair_order")
def create_repair_order(
    customer_first_name: str,
    customer_last_name: str,
    vin: str,
    technician_first_name: str,
    technician_last_name: str,
    service_bay_number: int,
    job_description: str,
    db: Session = Depends(get_db)
):
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find customer
    customer = db.query(Customer).filter(
        Customer.first_name == customer_first_name,
        Customer.last_name == customer_last_name
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    # Find vehicle
    vehicle = db.query(Vehicle).filter(Vehicle.vin == vin).first()
    
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Find technician
    technician = db.query(Technician).filter(
        Technician.first_name == technician_first_name,
        Technician.last_name == technician_last_name
    ).first()
    
    if not technician:
        raise HTTPException(status_code=404, detail="Technician not found")
    
    # Find service bay
    service_bay = db.query(ServiceBay).filter(
        ServiceBay.bay_number == service_bay_number
    ).first()
    
    if not service_bay:
        raise HTTPException(status_code=404, detail="Service bay not found")
    
    # Create repair order
    repair_order = RepairOrder(
        customer_id=customer.id,
        vehicle_id=vehicle.id,
        technician_id=technician.id,
        service_bay_id=service_bay.id,
        status="In Progress",
        notes=job_description,
        created_by=current_user_id
    )
    
    db.add(repair_order)
    db.commit()
    db.refresh(repair_order)
    
    # Add repair order item
    repair_order_item = RepairOrderItem(
        repair_order_id=repair_order.id,
        description=job_description,
        item_type="Service"
    )
    
    db.add(repair_order_item)
    db.commit()
    db.refresh(repair_order_item)
    
    return repair_order
