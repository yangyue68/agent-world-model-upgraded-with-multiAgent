import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Pydantic Models for Requests/Responses ---

class AddItemRequest(BaseModel):
    product_id: int
    quantity: int

class CartItemResponse(BaseModel):
    id: int
    cart_id: int
    product_id: int
    quantity: int

class ApplyCouponRequest(BaseModel):
    coupon_code: str

class CouponCartResponse(BaseModel):
    id: int
    cart_id: int
    coupon_id: int

class SetDeliveryRequest(BaseModel):
    delivery_option_name: str
    zip_code: str

class CartDeliveryResponse(BaseModel):
    id: int
    cart_id: int
    delivery_option_id: int
    zip_code: str

class ProductInfo(BaseModel):
    id: int
    name: str

class CouponInfo(BaseModel):
    id: int
    code: str
    description: str

class DeliveryOptionInfo(BaseModel):
    id: int
    name: str
    description: str


# --- Helper Functions ---

def get_current_user_id() -> int:
    """Reads the simulated logged-in actor from environment."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

def verify_cart_ownership(db, cart_id: int, user_id: int):
    """Verifies that the cart belongs to the current user and is active."""
    stmt = text("""
        SELECT id, status FROM carts 
        WHERE id = :cart_id AND user_id = :user_id
    """)
    result = db.execute(stmt, {"cart_id": cart_id, "user_id": user_id}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Cart not found or does not belong to user")
    
    # Optionally check status if business logic requires it (e.g., cannot modify checked_out cart)
    # The spec says "active cart", so we enforce it.
    if result.status != 'active':
        raise HTTPException(status_code=400, detail="Cart is not active")
        
    return True


# --- Endpoints ---

@app.post("/carts/{cart_id}/items", response_model=CartItemResponse, operation_id="add_item_to_cart")
def add_item_to_cart(
    cart_id: int,
    request: AddItemRequest,
    db=Depends(get_db)
):
    user_id = get_current_user_id()
    verify_cart_ownership(db, cart_id, user_id)

    # Check if product exists
    prod_stmt = text("SELECT id, stock_quantity FROM products WHERE id = :pid")
    prod_res = db.execute(prod_stmt, {"pid": request.product_id}).fetchone()
    if not prod_res:
        raise HTTPException(status_code=404, detail="Product not found")
    
    if prod_res.stock_quantity < request.quantity:
        raise HTTPException(status_code=400, detail="Insufficient stock")

    # Check if item already in cart
    existing_stmt = text("""
        SELECT id, quantity FROM cart_items 
        WHERE cart_id = :cid AND product_id = :pid
    """)
    existing_res = db.execute(existing_stmt, {"cid": cart_id, "pid": request.product_id}).fetchone()

    if existing_res:
        # Update quantity
        new_qty = existing_res.quantity + request.quantity
        update_stmt = text("""
            UPDATE cart_items 
            SET quantity = :new_qty, updated_at = CURRENT_TIMESTAMP 
            WHERE id = :id
        """)
        db.execute(update_stmt, {"new_qty": new_qty, "id": existing_res.id})
        db.commit()
        
        # Fetch updated row to return correct data structure
        fetch_stmt = text("SELECT id, cart_id, product_id, quantity FROM cart_items WHERE id = :id")
        final_row = db.execute(fetch_stmt, {"id": existing_res.id}).fetchone()
        return CartItemResponse(**final_row._asdict())
    else:
        # Insert new item
        insert_stmt = text("""
            INSERT INTO cart_items (cart_id, product_id, quantity, created_at, updated_at)
            VALUES (:cid, :pid, :qty, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        res = db.execute(insert_stmt, {
            "cid": cart_id, 
            "pid": request.product_id, 
            "qty": request.quantity
        })
        db.commit()
        
        # Get the inserted ID
        last_id = res.lastrowid
        
        fetch_stmt = text("SELECT id, cart_id, product_id, quantity FROM cart_items WHERE id = :id")
        final_row = db.execute(fetch_stmt, {"id": last_id}).fetchone()
        return CartItemResponse(**final_row._asdict())


@app.post("/carts/{cart_id}/coupons", response_model=CouponCartResponse, operation_id="apply_coupon_to_cart")
def apply_coupon_to_cart(
    cart_id: int,
    request: ApplyCouponRequest,
    db=Depends(get_db)
):
    user_id = get_current_user_id()
    verify_cart_ownership(db, cart_id, user_id)

    # Find coupon by code
    coupon_stmt = text("""
        SELECT id, discount_type, discount_value, min_order_amount, is_active, valid_from, valid_until
        FROM coupons 
        WHERE code = :code
    """)
    coupon_res = db.execute(coupon_stmt, {"code": request.coupon_code}).fetchone()
    
    if not coupon_res:
        raise HTTPException(status_code=404, detail="Coupon not found")
    
    if coupon_res.is_active == 0:
        raise HTTPException(status_code=400, detail="Coupon is inactive")
    
    # Validate dates if present
    now_stmt = text("SELECT datetime('now') as now")
    now_res = db.execute(now_stmt).scalar()
    
    if coupon_res.valid_from and now_res < coupon_res.valid_from:
        raise HTTPException(status_code=400, detail="Coupon not yet valid")
    if coupon_res.valid_until and now_res > coupon_res.valid_until:
        raise HTTPException(status_code=400, detail="Coupon expired")

    # Calculate current cart total to check min_order_amount
    total_stmt = text("""
        SELECT COALESCE(SUM(ci.quantity * p.price), 0) as total
        FROM cart_items ci
        JOIN products p ON ci.product_id = p.id
        WHERE ci.cart_id = :cid
    """)
    total_res = db.execute(total_stmt, {"cid": cart_id}).scalar()
    
    if total_res < coupon_res.min_order_amount:
        raise HTTPException(status_code=400, detail=f"Order total ${total_res:.2f} below minimum ${coupon_res.min_order_amount:.2f}")

    # Check if coupon already applied
    existing_cc_stmt = text("""
        SELECT id FROM cart_coupons 
        WHERE cart_id = :cid AND coupon_id = :cpid
    """)
    existing_cc = db.execute(existing_cc_stmt, {"cid": cart_id, "cpid": coupon_res.id}).fetchone()
    
    if existing_cc:
        raise HTTPException(status_code=400, detail="Coupon already applied to this cart")

    # Apply coupon
    insert_cc_stmt = text("""
        INSERT INTO cart_coupons (cart_id, coupon_id, applied_at)
        VALUES (:cid, :cpid, CURRENT_TIMESTAMP)
    """)
    res = db.execute(insert_cc_stmt, {"cid": cart_id, "cpid": coupon_res.id})
    db.commit()
    
    last_id = res.lastrowid
    
    fetch_stmt = text("SELECT id, cart_id, coupon_id FROM cart_coupons WHERE id = :id")
    final_row = db.execute(fetch_stmt, {"id": last_id}).fetchone()
    
    return CouponCartResponse(**final_row._asdict())


@app.post("/carts/{cart_id}/delivery", response_model=CartDeliveryResponse, operation_id="set_cart_delivery")
def set_cart_delivery(
    cart_id: int,
    request: SetDeliveryRequest,
    db=Depends(get_db)
):
    user_id = get_current_user_id()
    verify_cart_ownership(db, cart_id, user_id)

    # Find delivery option by name
    do_stmt = text("SELECT id FROM delivery_options WHERE name = :name")
    do_res = db.execute(do_stmt, {"name": request.delivery_option_name}).fetchone()
    
    if not do_res:
        raise HTTPException(status_code=404, detail="Delivery option not found")
    
    delivery_option_id = do_res.id

    # Check if delivery already set for this cart
    cd_stmt = text("SELECT id FROM cart_delivery WHERE cart_id = :cid")
    cd_res = db.execute(cd_stmt, {"cid": cart_id}).fetchone()
    
    if cd_res:
        # Update existing
        update_cd_stmt = text("""
            UPDATE cart_delivery 
            SET delivery_option_id = :did, zip_code = :zc, updated_at = CURRENT_TIMESTAMP
            WHERE cart_id = :cid
        """)
        db.execute(update_cd_stmt, {"did": delivery_option_id, "zc": request.zip_code, "cid": cart_id})
        db.commit()
        
        fetch_stmt = text("SELECT id, cart_id, delivery_option_id, zip_code FROM cart_delivery WHERE cart_id = :cid")
        final_row = db.execute(fetch_stmt, {"cid": cart_id}).fetchone()
        return CartDeliveryResponse(**final_row._asdict())
    else:
        # Insert new
        insert_cd_stmt = text("""
            INSERT INTO cart_delivery (cart_id, delivery_option_id, zip_code, created_at, updated_at)
            VALUES (:cid, :did, :zc, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        res = db.execute(insert_cd_stmt, {"cid": cart_id, "did": delivery_option_id, "zc": request.zip_code})
        db.commit()
        
        last_id = res.lastrowid
        fetch_stmt = text("SELECT id, cart_id, delivery_option_id, zip_code FROM cart_delivery WHERE id = :id")
        final_row = db.execute(fetch_stmt, {"id": last_id}).fetchone()
        return CartDeliveryResponse(**final_row._asdict())


@app.get("/products", response_model=List[ProductInfo], operation_id="find_products")
def find_products(name: str, db=Depends(get_db)):
    stmt = text("SELECT id, name FROM products WHERE name LIKE :pattern")
    results = db.execute(stmt, {"pattern": f"%{name}%"}).fetchall()
    return [ProductInfo(**r._asdict()) for r in results]


@app.get("/coupons", response_model=List[CouponInfo], operation_id="find_coupons")
def find_coupons(code: str, db=Depends(get_db)):
    stmt = text("SELECT id, code, description FROM coupons WHERE code = :code")
    results = db.execute(stmt, {"code": code}).fetchall()
    return [CouponInfo(**r._asdict()) for r in results]


@app.get("/delivery_options", response_model=List[DeliveryOptionInfo], operation_id="find_delivery_options")
def find_delivery_options(name: str, db=Depends(get_db)):
    stmt = text("SELECT id, name, description FROM delivery_options WHERE name LIKE :pattern")
    results = db.execute(stmt, {"pattern": f"%{name}%"}).fetchall()
    return [DeliveryOptionInfo(**r._asdict()) for r in results]
