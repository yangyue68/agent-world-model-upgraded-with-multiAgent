import os
from typing import List, Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, func, case
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
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Brand(Base):
    __tablename__ = "brands"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    model_number = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    price = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    brand = relationship("Brand")
    category = relationship("Category")
    specs = relationship("ProductSpec", back_populates="product")

class ProductSpec(Base):
    __tablename__ = "product_specs"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    spec_name = Column(String, nullable=False)
    spec_value = Column(String, nullable=False)
    spec_unit = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    product = relationship("Product", back_populates="specs")

class Comparison(Base):
    __tablename__ = "comparisons"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ComparisonItem(Base):
    __tablename__ = "comparison_items"
    id = Column(Integer, primary_key=True, index=True)
    comparison_id = Column(Integer, ForeignKey("comparisons.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class Spec(BaseModel):
    spec_name: str
    spec_value: str
    spec_unit: Optional[str] = None

class ProductDetails(BaseModel):
    id: int
    name: str
    description: str
    price: float
    specs: List[Spec]

class ProductSearchResult(BaseModel):
    id: int
    name: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/products/{product_id}", response_model=ProductDetails, operation_id="get_product_details")
def get_product_details(product_id: int, db = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    specs = []
    for spec in product.specs:
        specs.append(Spec(
            spec_name=spec.spec_name,
            spec_value=spec.spec_value,
            spec_unit=spec.spec_unit
        ))
    
    return ProductDetails(
        id=product.id,
        name=product.name,
        description=product.description,
        price=product.price,
        specs=specs
    )

@app.get("/products", response_model=List[ProductSearchResult], operation_id="find_products_by_name")
def find_products_by_name(name: str, db = Depends(get_db)):
    products = db.query(Product).filter(Product.name.contains(name)).all()
    return [
        ProductSearchResult(id=product.id, name=product.name)
        for product in products
    ]
