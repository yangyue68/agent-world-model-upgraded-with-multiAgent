import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=False, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String, nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=False, default="pending")
    estimated_delivery_date = Column(Date)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())

class OrderTrackingHistory(Base):
    __tablename__ = "order_tracking_history"
    
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    status = Column(String, nullable=False)
    status_description = Column(Text)
    location = Column(String)
    estimated_delivery_date = Column(Date)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())

# Pydantic Models
class TrackingEvent(BaseModel):
    id: int
    status: str
    status_description: str
    location: str
    estimated_delivery_date: Optional[str] = None
    created_at: str

class OrderTrackingHistoryResponse(BaseModel):
    order_number: str
    current_status: str
    estimated_delivery_date: Optional[str] = None
    tracking_events: List[TrackingEvent]

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/orders/{order_number}/tracking-history", response_model=OrderTrackingHistoryResponse, operation_id="get_order_tracking_history")
def get_order_tracking_history(order_number: str, db: Session = Depends(get_db)):
    # Find the order by order_number
    order = db.query(Order).filter(Order.order_number == order_number).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Get all tracking events for this order, ordered by created_at
    tracking_events = db.query(OrderTrackingHistory).filter(
        OrderTrackingHistory.order_id == order.id
    ).order_by(OrderTrackingHistory.created_at).all()
    
    # Convert tracking events to Pydantic model
    events = [
        TrackingEvent(
            id=event.id,
            status=event.status,
            status_description=event.status_description,
            location=event.location,
            estimated_delivery_date=event.estimated_delivery_date.isoformat() if event.estimated_delivery_date else None,
            created_at=event.created_at.isoformat() if event.created_at else None
        ) for event in tracking_events
    ]
    
    # Return the response
    return OrderTrackingHistoryResponse(
        order_number=order.order_number,
        current_status=order.status,
        estimated_delivery_date=order.estimated_delivery_date.isoformat() if order.estimated_delivery_date else None,
        tracking_events=events
    )
