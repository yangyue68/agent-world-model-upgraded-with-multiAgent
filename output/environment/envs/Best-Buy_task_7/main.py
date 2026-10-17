import os
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Float, UniqueConstraint
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
    email = Column(String)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    
    id = Column(Integer, primary_key=True, index=True)
    model = Column(String, unique=True, nullable=False)
    name = Column(Text, nullable=False)
    brand = Column(String, nullable=False)
    price = Column(Float)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Wishlist(Base):
    __tablename__ = "wishlists"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, default="Default Wishlist")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User")
    items = relationship("WishlistItem", back_populates="wishlist")

class WishlistItem(Base):
    __tablename__ = "wishlist_items"
    
    id = Column(Integer, primary_key=True, index=True)
    wishlist_id = Column(Integer, ForeignKey("wishlists.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    wishlist = relationship("Wishlist", back_populates="items")
    product = relationship("Product")

# Pydantic Models
class WishlistResponse(BaseModel):
    wishlist_name: str

class AddProductToWishlistRequest(BaseModel):
    model: str

class AddProductToWishlistResponse(BaseModel):
    message: str
    wishlist_item_id: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/users/{user_id}/default_wishlist", response_model=WishlistResponse, operation_id="get_user_default_wishlist")
def get_user_default_wishlist(user_id: int, db = None):
    if db is None:
        db = next(get_db())
    
    wishlist = db.query(Wishlist).filter(
        Wishlist.user_id == user_id,
        Wishlist.name == "Default Wishlist"
    ).first()
    
    if not wishlist:
        raise HTTPException(status_code=404, detail="Default wishlist not found")
    
    return WishlistResponse(wishlist_name=wishlist.name)

@app.post("/users/{user_id}/wishlists/{wishlist_name}/items", response_model=AddProductToWishlistResponse, operation_id="add_product_to_wishlist")
def add_product_to_wishlist(user_id: int, wishlist_name: str, payload: AddProductToWishlistRequest, db = None):
    if db is None:
        db = next(get_db())
    
    # Get the current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if current_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # Find the wishlist
    wishlist = db.query(Wishlist).filter(
        Wishlist.user_id == user_id,
        Wishlist.name == wishlist_name
    ).first()
    
    if not wishlist:
        # Create wishlist if it doesn't exist
        wishlist = Wishlist(user_id=user_id, name=wishlist_name)
        db.add(wishlist)
        db.commit()
        db.refresh(wishlist)
    
    # Find the product by model
    product = db.query(Product).filter(Product.model == payload.model).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Check if item already exists in wishlist
    existing_item = db.query(WishlistItem).filter(
        WishlistItem.wishlist_id == wishlist.id,
        WishlistItem.product_id == product.id
    ).first()
    
    if existing_item:
        raise HTTPException(status_code=400, detail="Product already in wishlist")
    
    # Add product to wishlist
    wishlist_item = WishlistItem(
        wishlist_id=wishlist.id,
        product_id=product.id
    )
    db.add(wishlist_item)
    db.commit()
    db.refresh(wishlist_item)
    
    return AddProductToWishlistResponse(
        message="Product added to wishlist",
        wishlist_item_id=wishlist_item.id
    )

@app.post("/users/{user_id}/wishlists/items", response_model=AddProductToWishlistResponse, operation_id="add_product_to_default_wishlist")
def add_product_to_default_wishlist(user_id: int, payload: AddProductToWishlistRequest, db = None):
    if db is None:
        db = next(get_db())
    
    # Get the current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if current_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # Find the default wishlist
    wishlist = db.query(Wishlist).filter(
        Wishlist.user_id == user_id,
        Wishlist.name == "Default Wishlist"
    ).first()
    
    if not wishlist:
        # Create default wishlist if it doesn't exist
        wishlist = Wishlist(user_id=user_id, name="Default Wishlist")
        db.add(wishlist)
        db.commit()
        db.refresh(wishlist)
    
    # Find the product by model
    product = db.query(Product).filter(Product.model == payload.model).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Check if item already exists in wishlist
    existing_item = db.query(WishlistItem).filter(
        WishlistItem.wishlist_id == wishlist.id,
        WishlistItem.product_id == product.id
    ).first()
    
    if existing_item:
        raise HTTPException(status_code=400, detail="Product already in wishlist")
    
    # Add product to wishlist
    wishlist_item = WishlistItem(
        wishlist_id=wishlist.id,
        product_id=product.id
    )
    db.add(wishlist_item)
    db.commit()
    db.refresh(wishlist_item)
    
    return AddProductToWishlistResponse(
        message="Product added to wishlist",
        wishlist_item_id=wishlist_item.id
    )
