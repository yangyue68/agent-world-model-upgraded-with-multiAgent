import os
from typing import List, Optional
from datetime import datetime
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, func, case, text
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
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
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
    name = Column(String, nullable=False)
    description = Column(String)
    price = Column(Float, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    average_rating = Column(Float, default=0.0)
    review_count = Column(Integer, default=0)
    is_prime_eligible = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    rating = Column(Integer, nullable=False)
    comment = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Pydantic Models
class ProductResponse(BaseModel):
    id: int
    name: str
    price: float
    average_rating: float
    review_count: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/search/products", response_model=List[ProductResponse], operation_id="search_products")
def search_products(
    query: str = Query(..., description="Search term to match against product names"),
    min_rating: Optional[float] = Query(4.5, description="Minimum average rating (default: 4.5)"),
    prime_only: Optional[bool] = Query(True, description="Whether to filter for Prime eligible items only (default: true)"),
    limit: Optional[int] = Query(3, description="Maximum number of results to return (default: 3)")
):
    db = next(get_db())
    
    # Build the base query
    stmt = db.query(Product).filter(Product.name.contains(query))
    
    # Apply minimum rating filter
    stmt = stmt.filter(Product.average_rating >= min_rating)
    
    # Apply Prime eligibility filter
    if prime_only:
        stmt = stmt.filter(Product.is_prime_eligible == True)
    
    # Order by rating descending, then by review count descending
    stmt = stmt.order_by(
        Product.average_rating.desc(),
        Product.review_count.desc()
    )
    
    # Limit results
    stmt = stmt.limit(limit)
    
    # Execute query
    products = stmt.all()
    
    # Format response
    result = []
    for product in products:
        result.append({
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "average_rating": product.average_rating,
            "review_count": product.review_count
        })
    
    return result
