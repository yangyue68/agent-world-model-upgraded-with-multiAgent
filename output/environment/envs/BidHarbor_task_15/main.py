import os
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, CheckConstraint, case
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
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    buyer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_number = Column(String, unique=True, nullable=False)
    status = Column(String, nullable=False, default="pending")
    total_amount = Column(Integer, nullable=False)
    currency = Column(String, nullable=False, default="USD")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    item_name = Column(String, nullable=False)
    item_description = Column(Text)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Shipment(Base):
    __tablename__ = "shipments"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    carrier = Column(String, nullable=False)
    tracking_number = Column(String, nullable=False)
    shipped_at = Column(DateTime)
    estimated_delivery = Column(DateTime)
    delivered_at = Column(DateTime)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Dispute(Base):
    __tablename__ = "disputes"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    opened_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id"))
    dispute_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="open")
    description = Column(Text, nullable=False)
    resolution = Column(Text)
    opened_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class DisputeEvidence(Base):
    __tablename__ = "dispute_evidence"
    id = Column(Integer, primary_key=True)
    dispute_id = Column(Integer, ForeignKey("disputes.id"), nullable=False)
    evidence_type = Column(String, nullable=False)
    reference_id = Column(Integer)
    content = Column(Text)
    file_path = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Request and Response Models
class DisputeRequest(BaseModel):
    order_id: int
    dispute_type: str
    description: str
    tracking_number: Optional[str] = None
    message_ids: Optional[List[int]] = []

class DisputeResponse(BaseModel):
    id: int
    order_id: int
    opened_by: int
    dispute_type: str
    status: str
    description: str
    opened_at: datetime

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/disputes", response_model=DisputeResponse, operation_id="open_dispute")
def open_dispute(dispute: DisputeRequest):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Validate dispute type
    valid_dispute_types = ["item_not_received", "item_not_as_described", "damaged_item", "late_delivery", "other"]
    if dispute.dispute_type not in valid_dispute_types:
        raise HTTPException(status_code=400, detail="Invalid dispute type")
    
    # Check if order exists and belongs to current user
    db = next(get_db())
    order = db.query(Order).filter(Order.id == dispute.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.buyer_id != current_user_id:
        raise HTTPException(status_code=403, detail="Access denied to this order")
    
    # Create dispute
    new_dispute = Dispute(
        order_id=dispute.order_id,
        opened_by=current_user_id,
        dispute_type=dispute.dispute_type,
        description=dispute.description
    )
    db.add(new_dispute)
    db.commit()
    db.refresh(new_dispute)
    
    # Add tracking number as evidence if provided
    if dispute.tracking_number:
        evidence = DisputeEvidence(
            dispute_id=new_dispute.id,
            evidence_type="tracking_number",
            content=f"Tracking number: {dispute.tracking_number}"
        )
        db.add(evidence)
    
    # Add messages as evidence if provided
    if dispute.message_ids:
        for msg_id in dispute.message_ids:
            message = db.query(Message).filter(Message.id == msg_id).first()
            if message and message.order_id == dispute.order_id:
                evidence = DisputeEvidence(
                    dispute_id=new_dispute.id,
                    evidence_type="message",
                    reference_id=msg_id
                )
                db.add(evidence)
    
    db.commit()
    
    return DisputeResponse(
        id=new_dispute.id,
        order_id=new_dispute.order_id,
        opened_by=new_dispute.opened_by,
        dispute_type=new_dispute.dispute_type,
        status=new_dispute.status,
        description=new_dispute.description,
        opened_at=new_dispute.opened_at
    )
