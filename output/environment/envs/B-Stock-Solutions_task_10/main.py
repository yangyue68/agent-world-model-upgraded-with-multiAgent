import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
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
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String, unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, nullable=False, default="Pending")
    total_amount = Column(Integer, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Shipment(Base):
    __tablename__ = "shipments"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), unique=True)
    tracking_number = Column(String)
    carrier = Column(String)
    status = Column(String, nullable=False, default="Pending")
    shipped_at = Column(DateTime)
    delivered_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Request and Response Models
class ShipmentUpdateRequest(BaseModel):
    tracking_number: str
    carrier: str
    status: str

class ShipmentResponse(BaseModel):
    id: int
    order_id: int
    tracking_number: Optional[str]
    carrier: Optional[str]
    status: str
    shipped_at: Optional[str]
    delivered_at: Optional[str]
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

@app.put("/orders/{order_id}/shipment", response_model=ShipmentResponse, operation_id="update_shipment_tracking")
async def update_shipment_tracking(
    order_id: str = Path(..., description="The order number (e.g., 'ORD-789012') for which to update shipment tracking"),
    shipment_data: ShipmentUpdateRequest = None,
    db = None
):
    if db is None:
        db = next(get_db())
    
    # Get the order
    order = db.query(Order).filter(Order.order_number == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Get the shipment
    shipment = db.query(Shipment).filter(Shipment.order_id == order.id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found for this order")
    
    # Update shipment details
    shipment.tracking_number = shipment_data.tracking_number
    shipment.carrier = shipment_data.carrier
    shipment.status = shipment_data.status
    
    # Set shipped_at if status is In Transit
    if shipment_data.status == "In Transit":
        shipment.shipped_at = datetime.utcnow()
    
    # Set delivered_at if status is Delivered
    if shipment_data.status == "Delivered":
        shipment.delivered_at = datetime.utcnow()
    
    # Update timestamps
    shipment.updated_at = datetime.utcnow()
    
    # Save changes
    db.commit()
    db.refresh(shipment)
    
    # Prepare response
    response = {
        "id": shipment.id,
        "order_id": shipment.order_id,
        "tracking_number": shipment.tracking_number,
        "carrier": shipment.carrier,
        "status": shipment.status,
        "shipped_at": shipment.shipped_at.isoformat() if shipment.shipped_at else None,
        "delivered_at": shipment.delivered_at.isoformat() if shipment.delivered_at else None,
        "created_at": shipment.created_at.isoformat(),
        "updated_at": shipment.updated_at.isoformat()
    }
    
    return response
