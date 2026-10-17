import os
from datetime import datetime, date
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Text, ForeignKey, CheckConstraint
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
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    zip = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = "vehicles"
    
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    trim = Column(String)
    color = Column(String)
    mileage = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Quote(Base):
    __tablename__ = "quotes"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    price = Column(Float, nullable=False)
    discount = Column(Float, default=0)
    expiration_date = Column(Date, nullable=False)
    status = Column(String, default="draft")
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    customer = relationship("Customer")
    vehicle = relationship("Vehicle")
    user = relationship("User")

# Pydantic Models
class QuoteCreate(BaseModel):
    customer_name: str
    vehicle_vin: str
    price: float
    discount: Optional[float] = 0
    expiration_date: str

class QuoteResponse(BaseModel):
    id: int
    customer_id: int
    vehicle_id: int
    user_id: int
    price: float
    discount: float
    expiration_date: str
    status: str
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

@app.post("/quotes", response_model=QuoteResponse, operation_id="create_quote")
def create_quote(quote: QuoteCreate):
    db = next(get_db())
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if current_user_id == 0:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Find customer by name
    customer_name_parts = quote.customer_name.split(' ', 1)
    first_name = customer_name_parts[0]
    last_name = customer_name_parts[1] if len(customer_name_parts) > 1 else ""
    
    customer = db.query(Customer).filter(
        Customer.first_name == first_name,
        Customer.last_name == last_name
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    # Find vehicle by VIN
    vehicle = db.query(Vehicle).filter(Vehicle.vin == quote.vehicle_vin).first()
    
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    
    # Parse expiration date
    expiration_date = datetime.strptime(quote.expiration_date, "%Y-%m-%d").date()
    
    # Create new quote
    db_quote = Quote(
        customer_id=customer.id,
        vehicle_id=vehicle.id,
        user_id=current_user_id,
        price=quote.price,
        discount=quote.discount,
        expiration_date=expiration_date,
        status="draft"
    )
    
    db.add(db_quote)
    db.commit()
    db.refresh(db_quote)
    
    # Convert datetime to string for response
    return QuoteResponse(
        id=db_quote.id,
        customer_id=db_quote.customer_id,
        vehicle_id=db_quote.vehicle_id,
        user_id=db_quote.user_id,
        price=db_quote.price,
        discount=db_quote.discount,
        expiration_date=str(db_quote.expiration_date),
        status=db_quote.status,
        created_at=db_quote.created_at.isoformat() if db_quote.created_at else None,
        updated_at=db_quote.updated_at.isoformat() if db_quote.updated_at else None
    )
