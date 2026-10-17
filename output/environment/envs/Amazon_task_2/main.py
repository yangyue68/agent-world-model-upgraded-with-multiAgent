import os
from typing import List, Optional
from datetime import datetime
from fastapi import FastAPI, Query, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Date, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Category(Base):
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    parent_id = Column(Integer, ForeignKey("categories.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    price = Column(Float, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class BestsellerRank(Base):
    __tablename__ = "bestseller_ranks"
    
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    rank = Column(Integer, nullable=False)
    list_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Review(Base):
    __tablename__ = "reviews"
    
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    rating = Column(Integer, nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    display_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Pydantic models
class ProductResponse(BaseModel):
    id: int
    title: str
    price: float
    review_count: int
    bestseller_rank: int
    
    class Config:
        from_attributes = True

class CategoryResponse(BaseModel):
    id: int
    name: str
    parent_id: Optional[int]
    
    class Config:
        from_attributes = True

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/products/bluetooth_speakers", response_model=List[ProductResponse], operation_id="find_bluetooth_speakers")
def find_bluetooth_speakers(
    max_price: float = Query(..., description="Maximum price threshold for the speakers"),
    min_reviews: int = Query(..., description="Minimum number of customer reviews required"),
    max_rank: int = Query(..., description="Maximum bestseller rank allowed (top N)"),
    category_path: str = Query(..., description="Full category path to filter by (e.g., 'Electronics > Speakers > Portable Bluetooth Speakers')"),
    db: SessionLocal = Depends(get_db)
):
    # Get category ID from path
    category_parts = category_path.split(" > ")
    current_category = None
    
    for part in category_parts:
        if current_category is None:
            current_category = db.query(Category).filter(Category.name == part).first()
        else:
            current_category = db.query(Category).filter(Category.name == part, Category.parent_id == current_category.id).first()
        
        if not current_category:
            raise HTTPException(status_code=404, detail="Category not found")
    
    # Build query
    stmt = db.query(
        Product.id,
        Product.title,
        Product.price,
        func.count(Review.id).label('review_count'),
        BestsellerRank.rank.label('bestseller_rank')
    ).join(BestsellerRank, Product.id == BestsellerRank.product_id)\
     .join(Review, Product.id == Review.product_id)\
     .filter(Product.category_id == current_category.id)\
     .filter(Product.price <= max_price)\
     .filter(BestsellerRank.rank <= max_rank)\
     .group_by(Product.id, Product.title, Product.price, BestsellerRank.rank)\
     .having(func.count(Review.id) >= min_reviews)\
     .order_by(BestsellerRank.rank)
    
    results = stmt.all()
    
    return [
        ProductResponse(
            id=row.id,
            title=row.title,
            price=row.price,
            review_count=row.review_count,
            bestseller_rank=row.bestseller_rank
        ) for row in results
    ]

@app.get("/categories/by_path", response_model=CategoryResponse, operation_id="get_category_by_path")
def get_category_by_path(path: str = Query(..., description="Full category path separated by ' > ' (e.g., 'Electronics > Speakers > Portable Bluetooth Speakers')"), db: SessionLocal = Depends(get_db)):
    category_parts = path.split(" > ")
    current_category = None
    
    for part in category_parts:
        if current_category is None:
            current_category = db.query(Category).filter(Category.name == part).first()
        else:
            current_category = db.query(Category).filter(Category.name == part, Category.parent_id == current_category.id).first()
        
        if not current_category:
            raise HTTPException(status_code=404, detail="Category not found")
    
    return CategoryResponse(
        id=current_category.id,
        name=current_category.name,
        parent_id=current_category.parent_id
    )
