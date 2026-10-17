import os
from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Path
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, ForeignKey, Text, func, case
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
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Carrier(Base):
    __tablename__ = "carriers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    tracking_url_template = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    order_number = Column(String, unique=True, nullable=False)
    order_date = Column(DateTime, nullable=False)
    total_amount = Column(Integer, nullable=False)
    currency = Column(String, default="USD")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    product_name = Column(String, nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Shipment(Base):
    __tablename__ = "shipments"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    carrier_id = Column(Integer, ForeignKey("carriers.id"))
    tracking_number = Column(String, nullable=False)
    status = Column(String, nullable=False)
    estimated_delivery_date = Column(Date)
    actual_delivery_date = Column(Date)
    shipped_at = Column(DateTime)
    delivered_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ShipmentEvent(Base):
    __tablename__ = "shipment_events"
    id = Column(Integer, primary_key=True, index=True)
    shipment_id = Column(Integer, ForeignKey("shipments.id"))
    event_time = Column(DateTime, nullable=False)
    status = Column(String, nullable=False)
    location = Column(String)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class ShipmentEventResponse(BaseModel):
    event_time: datetime
    status: str
    location: Optional[str] = None
    description: Optional[str] = None

class OrderDeliveryStatusResponse(BaseModel):
    order_id: str
    tracking_number: str
    carrier_name: str
    status: str
    estimated_delivery_date: date
    tracking_url: Optional[str] = None
    latest_shipment_event: Optional[ShipmentEventResponse] = None
    
    model_config = {"arbitrary_types_allowed": True}

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/orders/{order_id}/delivery-status", response_model=OrderDeliveryStatusResponse, operation_id="get_order_delivery_status")
def get_order_delivery_status(
    order_id: str = Path(..., description="The order identifier (e.g., '114-2468135-7913524')")
):
    db = next(get_db())
    
    # Get the order
    order = db.query(Order).filter(Order.order_number == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Get the shipment for this order
    shipment = db.query(Shipment).filter(Shipment.order_id == order.id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found for this order")
    
    # Get the carrier
    carrier = db.query(Carrier).filter(Carrier.id == shipment.carrier_id).first()
    if not carrier:
        raise HTTPException(status_code=404, detail="Carrier not found")
    
    # Get the latest shipment event
    latest_event = (
        db.query(ShipmentEvent)
        .filter(ShipmentEvent.shipment_id == shipment.id)
        .order_by(ShipmentEvent.event_time.desc())
        .first()
    )
    
    # Build the response
    response = OrderDeliveryStatusResponse(
        order_id=order.order_number,
        tracking_number=shipment.tracking_number,
        carrier_name=carrier.name,
        status=shipment.status,
        estimated_delivery_date=shipment.estimated_delivery_date,
        tracking_url=carrier.tracking_url_template.format(tracking_number=shipment.tracking_number) if carrier.tracking_url_template else None,
        latest_shipment_event=ShipmentEventResponse(
            event_time=latest_event.event_time,
            status=latest_event.status,
            location=latest_event.location,
            description=latest_event.description
        ) if latest_event else None
    )
    
    return response
