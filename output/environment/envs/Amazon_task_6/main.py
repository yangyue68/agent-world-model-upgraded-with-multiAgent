import os
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, DateTime, Integer, REAL, TEXT, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define models based on schema
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
    id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_date = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="pending")
    total_amount = Column(REAL, nullable=False, default=0.0)
    shipping_address = Column(TEXT)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("status IN ('pending', 'processing', 'shipped', 'delivered', 'cancelled', 'returned')"),)

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(REAL, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("quantity > 0"), CheckConstraint("unit_price >= 0"),)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(TEXT)
    price = Column(REAL, nullable=False)
    stock_quantity = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("price >= 0"), CheckConstraint("stock_quantity >= 0"),)

class OrderCancellation(Base):
    __tablename__ = "order_cancellations"
    id = Column(Integer, primary_key=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(TEXT, nullable=False)
    cancelled_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class CancelOrderRequest(BaseModel):
    reason: str

class CancelOrderResponse(BaseModel):
    order_id: str
    status: Literal["cancelled"]
    cancelled_at: datetime

# Create app
app = FastAPI()

@app.post("/orders/{order_id}/cancel", operation_id="cancel_order", response_model=CancelOrderResponse)
def cancel_order(
    order_id: str = Path(..., description="The unique identifier of the order to cancel"),
    body: CancelOrderRequest = None
):
    db = SessionLocal()
    try:
        # Get current user from environment
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
        
        # Check if order exists and is in delivered status
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        if order.status != "delivered":
            raise HTTPException(status_code=400, detail="Order must be in 'delivered' status to be cancelled")
        
        # Check if order belongs to current user
        if order.user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Check if order is already cancelled
        existing_cancel = db.query(OrderCancellation).filter(OrderCancellation.order_id == order_id).first()
        if existing_cancel:
            raise HTTPException(status_code=400, detail="Order is already cancelled")
        
        # Create cancellation record
        cancellation = OrderCancellation(
            order_id=order_id,
            user_id=current_user_id,
            reason=body.reason
        )
        db.add(cancellation)
        
        # Update order status
        order.status = "cancelled"
        db.commit()
        db.refresh(cancellation)
        
        return CancelOrderResponse(
            order_id=order_id,
            status="cancelled",
            cancelled_at=cancellation.cancelled_at
        )
    finally:
        db.close()
