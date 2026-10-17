import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Path, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String)
    model = Column(String)
    model_year = Column(Integer)
    license_plate = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class RepairOrder(Base):
    __tablename__ = "repair_orders"
    
    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String, unique=True, nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"))
    status = Column(String, nullable=False, default="open")
    description = Column(Text)
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime)
    created_by = Column(Integer, ForeignKey("users.id"))
    closed_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    vehicle = relationship("Vehicle")
    creator = relationship("User", foreign_keys=[created_by])
    closer = relationship("User", foreign_keys=[closed_by])

    __table_args__ = (
        CheckConstraint("status IN ('open', 'in_progress', 'completed', 'closed', 'cancelled')"),
    )

# Pydantic Models
class RepairOrderResponse(BaseModel):
    id: int
    order_number: str
    vehicle_id: int
    status: str
    description: str
    opened_at: str
    closed_at: Optional[str]
    created_by: int
    closed_by: Optional[int]
    vehicle: dict

class RepairOrderCloseRequest(BaseModel):
    closed_by: int

class RepairOrderCloseResponse(BaseModel):
    id: int
    order_number: str
    status: str
    closed_at: str
    closed_by: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/repair-orders/{order_id}/close", response_model=RepairOrderCloseResponse, operation_id="close_repair_order")
def close_repair_order(
    order_id: int,
    close_request: RepairOrderCloseRequest,
    db = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    if close_request.closed_by != current_user_id:
        raise HTTPException(status_code=400, detail="closed_by must match the authenticated user")
    
    repair_order = db.query(RepairOrder).filter(RepairOrder.id == order_id).first()
    
    if not repair_order:
        raise HTTPException(status_code=404, detail="Repair order not found")
    
    if repair_order.status not in ["in_progress", "open"]:
        raise HTTPException(status_code=400, detail="Repair order must be in 'in_progress' or 'open' status to be closable")
    
    repair_order.status = "closed"
    repair_order.closed_at = datetime.utcnow()
    repair_order.closed_by = current_user_id
    
    db.commit()
    db.refresh(repair_order)
    
    return {
        "id": repair_order.id,
        "order_number": repair_order.order_number,
        "status": repair_order.status,
        "closed_at": repair_order.closed_at.isoformat() + "Z",
        "closed_by": repair_order.closed_by
    }

@app.get("/repair-orders", response_model=RepairOrderResponse, operation_id="find_repair_order")
def find_repair_order(
    order_number: str,
    db = Depends(get_db)
):
    repair_order = db.query(RepairOrder).filter(RepairOrder.order_number == order_number).first()
    
    if not repair_order:
        raise HTTPException(status_code=404, detail="Repair order not found")
    
    vehicle_data = {
        "id": repair_order.vehicle.id,
        "vin": repair_order.vehicle.vin,
        "make": repair_order.vehicle.make,
        "model": repair_order.vehicle.model,
        "model_year": repair_order.vehicle.model_year,
        "license_plate": repair_order.vehicle.license_plate
    }
    
    return {
        "id": repair_order.id,
        "order_number": repair_order.order_number,
        "vehicle_id": repair_order.vehicle_id,
        "status": repair_order.status,
        "description": repair_order.description,
        "opened_at": repair_order.opened_at.isoformat() + "Z",
        "closed_at": repair_order.closed_at.isoformat() + "Z" if repair_order.closed_at else None,
        "created_by": repair_order.created_by,
        "closed_by": repair_order.closed_by,
        "vehicle": vehicle_data
    }
