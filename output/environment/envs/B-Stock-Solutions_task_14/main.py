import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String, unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=False, default="Pending")
    cancellation_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Shipment(Base):
    __tablename__ = "shipments"
    id = Column(Integer, primary_key=True, index=True)
    shipment_number = Column(String, unique=True, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    status = Column(String, nullable=False, default="Pending")
    cancellation_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class ShipmentResponse(BaseModel):
    id: int
    shipment_number: str
    order_id: int
    status: str
    cancellation_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class CancelShipmentRequest(BaseModel):
    cancellation_reason: str

class CancelShipmentResponse(BaseModel):
    shipment_id: int
    shipment_number: str
    order_id: int
    order_number: str
    status: str
    cancellation_reason: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/shipments/{shipment_number}", response_model=ShipmentResponse, operation_id="get_shipment_by_number")
def get_shipment_by_number(shipment_number: str):
    db = next(get_db())
    shipment = db.query(Shipment).filter(Shipment.shipment_number == shipment_number).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return shipment

@app.put("/shipments/{shipment_id}/cancel", response_model=CancelShipmentResponse, operation_id="cancel_shipment")
def cancel_shipment(shipment_id: int, request: CancelShipmentRequest):
    db = next(get_db())
    
    # Get the shipment
    shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    
    # Get the associated order
    order = db.query(Order).filter(Order.id == shipment.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Update shipment
    shipment.status = "Cancelled"
    shipment.cancellation_reason = request.cancellation_reason
    shipment.updated_at = datetime.utcnow()
    
    # Update order
    order.status = "Cancelled"
    order.cancellation_reason = request.cancellation_reason
    order.updated_at = datetime.utcnow()
    
    # Set the current user as the actor
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if current_user_id:
        # In a real app, we would set the created_by field here
        pass
    
    db.commit()
    db.refresh(shipment)
    db.refresh(order)
    
    return {
        "shipment_id": shipment.id,
        "shipment_number": shipment.shipment_number,
        "order_id": order.id,
        "order_number": order.order_number,
        "status": shipment.status,
        "cancellation_reason": shipment.cancellation_reason
    }
