import os
from datetime import datetime, date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Date, Text, ForeignKey, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
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
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_number = Column(String, unique=True, nullable=False)
    order_date = Column(Date, nullable=False)
    total_amount = Column(Float, nullable=False)
    status = Column(String, default="delivered")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User")

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_name = Column(String, nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    order = relationship("Order")

class ReturnReason(Base):
    __tablename__ = "return_reasons"
    id = Column(Integer, primary_key=True, index=True)
    reason_text = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Return(Base):
    __tablename__ = "returns"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    return_reason_id = Column(Integer, ForeignKey("return_reasons.id"), nullable=False)
    status = Column(String, default="initiated")
    refund_method = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    order = relationship("Order")
    order_item = relationship("OrderItem")
    user = relationship("User")
    return_reason = relationship("ReturnReason")

# Pydantic models
class ReturnReasonResponse(BaseModel):
    id: int
    reason_text: str
    created_at: datetime

    class Config:
        from_attributes = True

class OrderItemResponse(BaseModel):
    id: int
    order_id: int
    product_name: str
    quantity: int
    unit_price: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class OrderResponse(BaseModel):
    id: int
    user_id: int
    order_number: str
    order_date: date
    total_amount: float
    status: str
    created_at: datetime
    updated_at: datetime
    order_items: List[OrderItemResponse]

    class Config:
        from_attributes = True

class ReturnRequest(BaseModel):
    order_id: int
    order_item_id: int
    return_reason_id: int
    refund_method: str

class ReturnResponse(BaseModel):
    id: int
    order_id: int
    order_item_id: int
    user_id: int
    return_reason_id: int
    status: str
    refund_method: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# FastAPI app
app = FastAPI()

# Helper functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user_id():
    return int(os.environ.get("AWM_CURRENT_USER_ID", 0))

# API endpoints
@app.post("/returns", response_model=ReturnResponse, operation_id="initiate_return")
def initiate_return(return_request: ReturnRequest, db=Depends(get_db)):
    current_user_id = get_current_user_id()
    
    # Verify the order belongs to the current user
    order = db.query(Order).filter(Order.id == return_request.order_id).first()
    if not order or order.user_id != current_user_id:
        raise HTTPException(status_code=404, detail="Order not found or not owned by user")
    
    # Verify the order item exists and belongs to the order
    order_item = db.query(OrderItem).filter(
        OrderItem.id == return_request.order_item_id,
        OrderItem.order_id == return_request.order_id
    ).first()
    if not order_item:
        raise HTTPException(status_code=404, detail="Order item not found")
    
    # Verify the return reason exists
    return_reason = db.query(ReturnReason).filter(
        ReturnReason.id == return_request.return_reason_id
    ).first()
    if not return_reason:
        raise HTTPException(status_code=404, detail="Return reason not found")
    
    # Create the return
    new_return = Return(
        order_id=return_request.order_id,
        order_item_id=return_request.order_item_id,
        user_id=current_user_id,
        return_reason_id=return_request.return_reason_id,
        refund_method=return_request.refund_method
    )
    
    db.add(new_return)
    db.commit()
    db.refresh(new_return)
    
    return new_return

@app.get("/orders/{order_number}", response_model=OrderResponse, operation_id="get_order_by_number")
def get_order_by_number(order_number: str, db=Depends(get_db)):
    current_user_id = get_current_user_id()
    
    order = db.query(Order).filter(Order.order_number == order_number).first()
    if not order or order.user_id != current_user_id:
        raise HTTPException(status_code=404, detail="Order not found or not owned by user")
    
    return order

@app.get("/return_reasons", response_model=List[ReturnReasonResponse], operation_id="list_return_reasons")
def list_return_reasons(db=Depends(get_db)):
    return db.query(ReturnReason).all()
