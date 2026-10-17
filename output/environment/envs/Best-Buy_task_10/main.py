import os
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, Time, DateTime, ForeignKey, CheckConstraint, case
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
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Store(Base):
    __tablename__ = "stores"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    zip = Column(String)
    phone = Column(String)
    latitude = Column(Integer)
    longitude = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ProductCategory(Base):
    __tablename__ = "product_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    parent_id = Column(Integer, ForeignKey("product_categories.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    sku = Column(String, unique=True, nullable=False)
    model = Column(String, nullable=False)
    name = Column(String, nullable=False)
    brand = Column(String, nullable=False)
    category_id = Column(Integer, ForeignKey("product_categories.id"))
    description = Column(String)
    price = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class StoreInventory(Base):
    __tablename__ = "store_inventory"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ServiceType(Base):
    __tablename__ = "service_types"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    duration_minutes = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class AppointmentSlot(Base):
    __tablename__ = "appointment_slots"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"))
    service_type_id = Column(Integer, ForeignKey("service_types.id"))
    slot_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    is_available = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    CheckConstraint("is_available IN (0,1)")

class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    slot_id = Column(Integer, ForeignKey("appointment_slots.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    status = Column(String, default="scheduled")
    notes = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Pydantic models
class AppointmentSlotResponse(BaseModel):
    id: int
    slot_date: str
    start_time: str
    end_time: str

class ProductResponse(BaseModel):
    id: int
    sku: str
    model: str
    name: str
    brand: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/stores/{store_id}/available_appointment_slots", response_model=List[AppointmentSlotResponse], operation_id="find_available_appointment_slots")
def find_available_appointment_slots(
    store_id: int,
    service_type_name: str,
    days_ahead: Optional[int] = 7,
    db=None
):
    if db is None:
        db = next(get_db())
    
    # Get service type ID
    service_type = db.query(ServiceType).filter(ServiceType.name == service_type_name).first()
    if not service_type:
        raise HTTPException(status_code=404, detail="Service type not found")
    
    # Calculate date range
    today = datetime.now().date()
    end_date = today + timedelta(days=days_ahead)
    
    # Query available slots
    slots = db.query(AppointmentSlot).filter(
        AppointmentSlot.store_id == store_id,
        AppointmentSlot.service_type_id == service_type.id,
        AppointmentSlot.slot_date >= today,
        AppointmentSlot.slot_date <= end_date,
        AppointmentSlot.is_available == 1
    ).order_by(AppointmentSlot.slot_date, AppointmentSlot.start_time).all()
    
    # Format response
    result = []
    for slot in slots:
        result.append({
            "id": slot.id,
            "slot_date": slot.slot_date.isoformat(),
            "start_time": slot.start_time.strftime("%H:%M:%S"),
            "end_time": slot.end_time.strftime("%H:%M:%S")
        })
    
    return result

@app.get("/products", response_model=List[ProductResponse], operation_id="find_products_by_model")
def find_products_by_model(
    model: str,
    db=None
):
    if db is None:
        db = next(get_db())
    
    products = db.query(Product).filter(Product.model == model).all()
    
    result = []
    for product in products:
        result.append({
            "id": product.id,
            "sku": product.sku,
            "model": product.model,
            "name": product.name,
            "brand": product.brand
        })
    
    return result
