import os
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, ForeignKey, CheckConstraint, Index, case
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

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    price = Column(Float, nullable=False)
    currency = Column(String, default="USD")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Wishlist(Base):
    __tablename__ = "wishlists"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, default="Default")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class WishlistItem(Base):
    __tablename__ = "wishlist_items"
    id = Column(Integer, primary_key=True, index=True)
    wishlist_id = Column(Integer, ForeignKey("wishlists.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PriceAlert(Base):
    __tablename__ = "price_alerts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    target_price = Column(Float, nullable=False)
    currency = Column(String, default="USD")
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# FastAPI app
app = FastAPI()

# Pydantic models
class AddProductToWishlistRequest(BaseModel):
    product_id: int

class AddProductToWishlistResponse(BaseModel):
    wishlist_item_id: int

class CreatePriceAlertRequest(BaseModel):
    product_id: int
    target_price: float

class CreatePriceAlertResponse(BaseModel):
    price_alert_id: int

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
@app.post("/users/{user_id}/wishlists/{wishlist_id}/items", response_model=AddProductToWishlistResponse, operation_id="add_product_to_wishlist")
def add_product_to_wishlist(user_id: int, wishlist_id: int, request: AddProductToWishlistRequest):
    current_user_id = get_current_user_id()
    if current_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    db = next(get_db())
    
    # Verify wishlist belongs to user
    wishlist = db.query(Wishlist).filter(Wishlist.id == wishlist_id, Wishlist.user_id == user_id).first()
    if not wishlist:
        raise HTTPException(status_code=404, detail="Wishlist not found")
    
    # Verify product exists
    product = db.query(Product).filter(Product.id == request.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Check if product is already in wishlist
    existing_item = db.query(WishlistItem).filter(
        WishlistItem.wishlist_id == wishlist_id,
        WishlistItem.product_id == request.product_id
    ).first()
    
    if existing_item:
        raise HTTPException(status_code=409, detail="Product already in wishlist")
    
    # Create new wishlist item
    wishlist_item = WishlistItem(
        wishlist_id=wishlist_id,
        product_id=request.product_id
    )
    db.add(wishlist_item)
    db.commit()
    db.refresh(wishlist_item)
    
    return AddProductToWishlistResponse(wishlist_item_id=wishlist_item.id)

@app.post("/users/{user_id}/price_alerts", response_model=CreatePriceAlertResponse, operation_id="create_price_alert")
def create_price_alert(user_id: int, request: CreatePriceAlertRequest):
    current_user_id = get_current_user_id()
    if current_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    db = next(get_db())
    
    # Verify product exists
    product = db.query(Product).filter(Product.id == request.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Create new price alert
    price_alert = PriceAlert(
        user_id=user_id,
        product_id=request.product_id,
        target_price=request.target_price
    )
    db.add(price_alert)
    db.commit()
    db.refresh(price_alert)
    
    return CreatePriceAlertResponse(price_alert_id=price_alert.id)
