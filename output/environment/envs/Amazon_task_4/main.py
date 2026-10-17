import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

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
    updated_at = Column(DateTime, default=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    price = Column(Float, nullable=False)
    stock_quantity = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Cart(Base):
    __tablename__ = "carts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class CartItem(Base):
    __tablename__ = "cart_items"
    id = Column(Integer, primary_key=True, index=True)
    cart_id = Column(Integer, ForeignKey("carts.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Coupon(Base):
    __tablename__ = "coupons"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False)
    discount_amount = Column(Float, nullable=False)
    is_active = Column(Integer, default=1)
    valid_from = Column(DateTime)
    valid_until = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class CartCoupon(Base):
    __tablename__ = "cart_coupons"
    id = Column(Integer, primary_key=True, index=True)
    cart_id = Column(Integer, ForeignKey("carts.id"), nullable=False)
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=False)
    applied_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class CartItemRequest(BaseModel):
    product_id: int
    quantity: int = 1

class CartItemResponse(BaseModel):
    cart_item_id: int

class CartItemInfo(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    price: float

class CartContentsResponse(BaseModel):
    items: List[CartItemInfo]

class ApplyCouponRequest(BaseModel):
    coupon_code: str

class ApplyCouponResponse(BaseModel):
    success: bool
    discount_amount: Optional[float] = None

class CartSummaryResponse(BaseModel):
    total_amount: float
    discount_amount: float
    final_amount: float

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user_id():
    user_id = os.getenv("AWM_CURRENT_USER_ID")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return int(user_id)

@app.post("/cart/items", response_model=CartItemResponse, operation_id="add_item_to_cart")
def add_item_to_cart(item: CartItemRequest, db: Session = Depends(get_db)):
    current_user_id = get_current_user_id()
    
    # Get user's cart
    cart = db.query(Cart).filter(Cart.user_id == current_user_id).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")
    
    # Check if product exists
    product = db.query(Product).filter(Product.id == item.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Check if product is in stock
    if product.stock_quantity < item.quantity:
        raise HTTPException(status_code=400, detail="Insufficient stock")
    
    # Check if item already in cart
    cart_item = db.query(CartItem).filter(
        CartItem.cart_id == cart.id,
        CartItem.product_id == item.product_id
    ).first()
    
    if cart_item:
        cart_item.quantity += item.quantity
    else:
        cart_item = CartItem(
            cart_id=cart.id,
            product_id=item.product_id,
            quantity=item.quantity
        )
        db.add(cart_item)
    
    db.commit()
    db.refresh(cart_item)
    
    return {"cart_item_id": cart_item.id}

@app.get("/cart", response_model=CartContentsResponse, operation_id="get_cart_contents")
def get_cart_contents(db: Session = Depends(get_db)):
    current_user_id = get_current_user_id()
    
    # Get user's cart
    cart = db.query(Cart).filter(Cart.user_id == current_user_id).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")
    
    # Get cart items
    cart_items = db.query(CartItem).filter(CartItem.cart_id == cart.id).all()
    
    items = []
    for item in cart_items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if product:
            items.append({
                "product_id": product.id,
                "product_name": product.name,
                "quantity": item.quantity,
                "price": product.price
            })
    
    return {"items": items}

@app.post("/cart/coupons", response_model=ApplyCouponResponse, operation_id="apply_coupon_to_cart")
def apply_coupon_to_cart(coupon_request: ApplyCouponRequest, db: Session = Depends(get_db)):
    current_user_id = get_current_user_id()
    
    # Get user's cart
    cart = db.query(Cart).filter(Cart.user_id == current_user_id).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")
    
    # Find coupon
    coupon = db.query(Coupon).filter(Coupon.code == coupon_request.coupon_code).first()
    if not coupon:
        return {"success": False}
    
    # Check if coupon is active
    if not coupon.is_active:
        return {"success": False}
    
    # Check if coupon is valid
    now = datetime.utcnow()
    if coupon.valid_from and coupon.valid_from > now:
        return {"success": False}
    if coupon.valid_until and coupon.valid_until < now:
        return {"success": False}
    
    # Check if coupon already applied to this cart
    existing = db.query(CartCoupon).filter(
        CartCoupon.cart_id == cart.id,
        CartCoupon.coupon_id == coupon.id
    ).first()
    
    if existing:
        return {"success": True, "discount_amount": coupon.discount_amount}
    
    # Apply coupon
    cart_coupon = CartCoupon(
        cart_id=cart.id,
        coupon_id=coupon.id
    )
    db.add(cart_coupon)
    db.commit()
    
    return {"success": True, "discount_amount": coupon.discount_amount}

@app.get("/cart/summary", response_model=CartSummaryResponse, operation_id="get_cart_summary")
def get_cart_summary(db: Session = Depends(get_db)):
    current_user_id = get_current_user_id()
    
    # Get user's cart
    cart = db.query(Cart).filter(Cart.user_id == current_user_id).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")
    
    # Calculate total amount
    cart_items = db.query(CartItem).filter(CartItem.cart_id == cart.id).all()
    
    total_amount = 0.0
    for item in cart_items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if product:
            total_amount += product.price * item.quantity
    
    # Calculate discount amount
    discount_amount = 0.0
    applied_coupons = db.query(CartCoupon).filter(CartCoupon.cart_id == cart.id).all()
    
    for applied in applied_coupons:
        coupon = db.query(Coupon).filter(Coupon.id == applied.coupon_id).first()
        if coupon:
            discount_amount += coupon.discount_amount
    
    final_amount = max(0.0, total_amount - discount_amount)
    
    return {
        "total_amount": round(total_amount, 2),
        "discount_amount": round(discount_amount, 2),
        "final_amount": round(final_amount, 2)
    }
