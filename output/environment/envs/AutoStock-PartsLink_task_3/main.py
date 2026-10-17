import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, func, case, and_, text
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
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class VehicleMake(Base):
    __tablename__ = "vehicle_makes"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class VehicleModel(Base):
    __tablename__ = "vehicle_models"
    id = Column(Integer, primary_key=True, index=True)
    make_id = Column(Integer, ForeignKey("vehicle_makes.id"))
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    make = relationship("VehicleMake")

class VehicleTrim(Base):
    __tablename__ = "vehicle_trims"
    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(Integer, ForeignKey("vehicle_models.id"))
    name = Column(String, nullable=False)
    year = Column(Integer)
    drive_type = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    model = relationship("VehicleModel")

class Category(Base):
    __tablename__ = "categories"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    parent_id = Column(String, ForeignKey("categories.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    category_id = Column(String, ForeignKey("categories.id"))
    wholesale_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    category = relationship("Category")

class ProductApplication(Base):
    __tablename__ = "product_applications"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    trim_id = Column(Integer, ForeignKey("vehicle_trims.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    product = relationship("Product")
    trim = relationship("VehicleTrim")

class Warehouse(Base):
    __tablename__ = "warehouses"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    location = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class WarehouseBin(Base):
    __tablename__ = "warehouse_bins"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    bin_code = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    warehouse = relationship("Warehouse")

class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    bin_id = Column(Integer, ForeignKey("warehouse_bins.id"))
    on_hand_quantity = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    product = relationship("Product")
    bin = relationship("WarehouseBin")

# Response model
class CompatibleProductResponse(BaseModel):
    product_id: int
    sku: str
    name: str
    wholesale_price: float
    on_hand_quantity: int

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/vehicle-applications/compatible-products", response_model=List[CompatibleProductResponse], operation_id="find_compatible_brake_pad_kits")
def find_compatible_brake_pad_kits(
    make: str = Query(...),
    model: str = Query(...),
    year: int = Query(...),
    trim: str = Query(...),
    drive_type: str = Query(...),
    min_on_hand_quantity: Optional[int] = Query(None),
    max_wholesale_price: Optional[float] = Query(None),
):
    db = next(get_db())
    
    # Find the vehicle trim
    vehicle_trim = db.query(VehicleTrim).join(VehicleModel).join(VehicleMake)\
        .filter(
            VehicleMake.name == make,
            VehicleModel.name == model,
            VehicleTrim.year == year,
            VehicleTrim.name == trim,
            VehicleTrim.drive_type == drive_type
        ).first()
    
    if not vehicle_trim:
        return []
    
    # Build the query for compatible brake pad kits
    # First, get all products compatible with this trim that are brake pad kits
    compatible_products = db.query(Product.id.label('product_id'), Product.sku, Product.name, Product.wholesale_price)\
        .join(ProductApplication)\
        .filter(ProductApplication.trim_id == vehicle_trim.id)\
        .join(Category)\
        .filter(Category.id == 'BRAKE_PAD_KIT')
    
    # Apply filters
    if max_wholesale_price is not None:
        compatible_products = compatible_products.filter(Product.wholesale_price <= max_wholesale_price)
    
    # Get product IDs
    product_ids = [p.product_id for p in compatible_products.all()]
    
    if not product_ids:
        return []
    
    # For each product, find the maximum on-hand quantity across bins
    # Using a subquery to get the max quantity per product
    max_quantity_subq = db.query(
        Inventory.product_id,
        func.max(Inventory.on_hand_quantity).label('max_quantity')
    ).group_by(Inventory.product_id).subquery()
    
    # Join with products to get all relevant info
    final_query = db.query(
        Product.id.label('product_id'),
        Product.sku,
        Product.name,
        Product.wholesale_price,
        max_quantity_subq.c.max_quantity.label('on_hand_quantity')
    ).join(max_quantity_subq, Product.id == max_quantity_subq.c.product_id)\
     .filter(Product.id.in_(product_ids))
    
    # Apply minimum quantity filter
    if min_on_hand_quantity is not None:
        final_query = final_query.filter(max_quantity_subq.c.max_quantity >= min_on_hand_quantity)
    
    # Apply maximum price filter
    if max_wholesale_price is not None:
        final_query = final_query.filter(Product.wholesale_price <= max_wholesale_price)
    
    results = final_query.all()
    
    # Convert to response objects
    response = [
        CompatibleProductResponse(
            product_id=r.product_id,
            sku=r.sku,
            name=r.name,
            wholesale_price=r.wholesale_price,
            on_hand_quantity=r.on_hand_quantity
        ) for r in results
    ]
    
    return response
