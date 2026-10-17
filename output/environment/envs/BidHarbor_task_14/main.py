import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    buyer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_date = Column(DateTime, default=datetime.utcnow)
    status = Column(String, nullable=False, default="pending")
    total_amount = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    item_title = Column(String, nullable=False)
    item_description = Column(Text)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ReturnRequest(Base):
    __tablename__ = "return_requests"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ReturnRequestDamageDetail(Base):
    __tablename__ = "return_request_damage_details"
    id = Column(Integer, primary_key=True)
    return_request_id = Column(Integer, ForeignKey("return_requests.id"), nullable=False, unique=True)
    damage_description = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class ReturnRequestCreate(BaseModel):
    order_id: int
    reason: str

class ReturnRequestResponse(BaseModel):
    id: int
    order_id: int
    user_id: int
    reason: str
    status: str
    created_at: datetime
    updated_at: datetime

class DamageDetailsCreate(BaseModel):
    damage_description: str

class DamageDetailsResponse(BaseModel):
    id: int
    return_request_id: int
    damage_description: str
    created_at: datetime
    updated_at: datetime

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/return_requests", response_model=ReturnRequestResponse, operation_id="create_return_request")
def create_return_request(request: ReturnRequestCreate, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the user is the buyer of the order
    order = db.query(Order).filter(Order.id == request.order_id).first()
    if not order or order.buyer_id != current_user_id:
        raise HTTPException(status_code=403, detail="User is not authorized to create return request for this order")
    
    # Create the return request
    return_request = ReturnRequest(
        order_id=request.order_id,
        user_id=current_user_id,
        reason=request.reason
    )
    db.add(return_request)
    db.commit()
    db.refresh(return_request)
    
    return return_request

@app.post("/return_requests/{return_request_id}/damage_details", response_model=DamageDetailsResponse, operation_id="add_damage_details")
def add_damage_details(return_request_id: int, damage_details: DamageDetailsCreate, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the return request exists and belongs to the current user
    return_request = db.query(ReturnRequest).filter(ReturnRequest.id == return_request_id).first()
    if not return_request or return_request.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="User is not authorized to add damage details to this return request")
    
    # Check if damage details already exist for this return request
    existing_damage_detail = db.query(ReturnRequestDamageDetail).filter(ReturnRequestDamageDetail.return_request_id == return_request_id).first()
    if existing_damage_detail:
        raise HTTPException(status_code=400, detail="Damage details already exist for this return request")
    
    # Create the damage details
    damage_detail = ReturnRequestDamageDetail(
        return_request_id=return_request_id,
        damage_description=damage_details.damage_description
    )
    db.add(damage_detail)
    db.commit()
    db.refresh(damage_detail)
    
    return damage_detail
