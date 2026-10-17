import os
from typing import Optional, List
from datetime import datetime
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Brand(Base):
    __tablename__ = "brands"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    brand = relationship("Brand")
    category = relationship("Category")

class Store(Base):
    __tablename__ = "stores"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    zip_code = Column(String, nullable=False)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ProductPrice(Base):
    __tablename__ = "product_prices"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    price = Column(Float, nullable=False)
    sale_price = Column(Float)
    currency = Column(String, nullable=False, default="USD")
    effective_date = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    product = relationship("Product")

class ProductInventory(Base):
    __tablename__ = "product_inventory"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    pickup_available = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    product = relationship("Product")
    store = relationship("Store")

class LaptopSpecs(Base):
    __tablename__ = "laptop_specs"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, unique=True)
    cpu = Column(String, nullable=False)
    cpu_score = Column(Integer, nullable=False)
    ram_gb = Column(Integer, nullable=False)
    gpu = Column(String, nullable=False)
    gpu_score = Column(Integer, nullable=False)
    battery_life_hours = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    product = relationship("Product")

# Response model
class LaptopResult(BaseModel):
    model_name: str
    sale_price: float
    pickup_available: bool

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/laptops/filtered", response_model=List[LaptopResult], operation_id="find_filtered_laptops")
def find_filtered_laptops(
    cpu_score_min: Optional[int] = Query(None),
    ram_gb: Optional[int] = Query(None),
    gpu_score_min: Optional[int] = Query(None),
    battery_life_hours: Optional[float] = Query(None),
):
    db = next(get_db())
    
    # Build query
    query = db.query(
        Product.name.label("model_name"),
        ProductPrice.sale_price,
        ProductInventory.pickup_available
    ).join(LaptopSpecs, Product.id == LaptopSpecs.product_id)\
     .join(ProductPrice, Product.id == ProductPrice.product_id)\
     .join(ProductInventory, Product.id == ProductInventory.product_id)\
     .filter(Product.category_id == 2)  # Filter for laptops
    
    # Apply filters if provided
    if cpu_score_min is not None:
        query = query.filter(LaptopSpecs.cpu_score >= cpu_score_min)
    if ram_gb is not None:
        query = query.filter(LaptopSpecs.ram_gb >= ram_gb)
    if gpu_score_min is not None:
        query = query.filter(LaptopSpecs.gpu_score >= gpu_score_min)
    if battery_life_hours is not None:
        query = query.filter(LaptopSpecs.battery_life_hours >= battery_life_hours)
    
    # Order by lowest sale price
    query = query.order_by(ProductPrice.sale_price.asc())
    
    # Get top result
    result = query.first()
    
    if result:
        return [LaptopResult(
            model_name=result.model_name,
            sale_price=result.sale_price,
            pickup_available=bool(result.pickup_available)
        )]
    else:
        return []
