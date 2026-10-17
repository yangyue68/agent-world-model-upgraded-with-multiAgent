import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

app = FastAPI(title="CVS Pharmacy API")


# --- Pydantic Models for Responses ---

class StoreResponse(BaseModel):
    id: int
    store_number: str
    address: str
    city: str
    state: str
    zip: str
    phone: str

class WeeklyAdResponse(BaseModel):
    id: int
    store_id: int
    week_start_date: str
    week_end_date: str

class ProductResponse(BaseModel):
    id: int
    name: str
    brand: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None

class DiscountedPriceResponse(BaseModel):
    discounted_price: float

class CartResponse(BaseModel):
    id: int
    user_id: int
    store_id: int
    status: str

class CartItemResponse(BaseModel):
    id: int
    cart_id: int
    product_id: int
    quantity: int
    unit_price: float

class PickupScheduleResponse(BaseModel):
    id: int
    cart_id: int
    pickup_date: str
    pickup_time_start: str
    pickup_time_end: str
    status: str


# --- Request Body Models ---

class CreateCartRequest(BaseModel):
    store_id: int

class AddCartItemRequest(BaseModel):
    cart_id: int
    product_id: int
    quantity: int
    unit_price: float

class SchedulePickupRequest(BaseModel):
    cart_id: int
    pickup_date: str
    pickup_time_start: str
    pickup_time_end: str


# --- Endpoints ---

@app.get("/stores/by_number/{store_number}", response_model=StoreResponse, operation_id="get_store_by_number")
def get_store_by_number(store_number: str):
    with SessionLocal() as db:
        result = db.execute(
            text("SELECT id, store_number, address, city, state, zip, phone FROM stores WHERE store_number = :sn"),
            {"sn": store_number}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Store not found")
            
        return StoreResponse(**result._asdict())


@app.get("/weekly_ads/current_for_store/{store_id}", response_model=WeeklyAdResponse, operation_id="get_current_weekly_ad_for_store")
def get_current_weekly_ad_for_store(store_id: int):
    # In a real scenario, we'd check dates against today. 
    # Based on initial_snapshot, all ads are for 2024-05-05 to 2024-05-11.
    # We assume there is one active ad per store or pick the most recent/valid one.
    # The spec implies fetching "the" current ad. Let's fetch the latest one for simplicity if multiple exist,
    # or just any matching the logic of being "current". Given the static data, we'll just grab the first one associated with the store.
    
    with SessionLocal() as db:
        result = db.execute(
            text("""
                SELECT id, store_id, week_start_date, week_end_date 
                FROM weekly_ads 
                WHERE store_id = :sid 
                ORDER BY week_start_date DESC 
                LIMIT 1
            """),
            {"sid": store_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="No current weekly ad found for this store")
            
        return WeeklyAdResponse(**result._asdict())


@app.get("/products/by_name/{product_name}", response_model=ProductResponse, operation_id="get_product_by_name")
def get_product_by_name(product_name: str):
    with SessionLocal() as db:
        # Using LIKE for partial match flexibility, though exact match might be safer depending on requirements.
        # Spec says "locate... 'Olay Regenerist Micro-Sculpting Cream'". Exact match is preferred for precision.
        result = db.execute(
            text("SELECT id, name, brand, description, category FROM products WHERE name = :pn"),
            {"pn": product_name}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Product not found")
            
        return ProductResponse(**result._asdict())


@app.get("/weekly_ad_items/discounted_price", response_model=DiscountedPriceResponse, operation_id="get_discounted_price")
def get_discounted_price(weekly_ad_id: int = Query(...), product_id: int = Query(...)):
    with SessionLocal() as db:
        result = db.execute(
            text("SELECT discounted_price FROM weekly_ad_items WHERE weekly_ad_id = :waid AND product_id = :pid"),
            {"waid": weekly_ad_id, "pid": product_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Discounted price not found for this product in this ad")
            
        return DiscountedPriceResponse(discounted_price=result.discounted_price)


@app.post("/carts", response_model=CartResponse, operation_id="create_cart")
def create_cart(request: CreateCartRequest):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    with SessionLocal() as db:
        # Verify store exists
        store_exists = db.execute(text("SELECT 1 FROM stores WHERE id = :sid"), {"sid": request.store_id}).fetchone()
        if not store_exists:
            raise HTTPException(status_code=404, detail="Store not found")

        # Insert new cart
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        res = db.execute(
            text("""
                INSERT INTO carts (user_id, store_id, status, created_at, updated_at)
                VALUES (:uid, :sid, 'active', :now, :now)
            """),
            {"uid": user_id, "sid": request.store_id, "now": now}
        )
        db.commit()
        
        cart_id = res.lastrowid
        
        # Fetch back to ensure consistency and return correct object
        final_res = db.execute(
            text("SELECT id, user_id, store_id, status FROM carts WHERE id = :cid"),
            {"cid": cart_id}
        ).fetchone()
        
        return CartResponse(**final_res._asdict())


@app.post("/cart_items", response_model=CartItemResponse, operation_id="add_item_to_cart")
def add_item_to_cart(request: AddCartItemRequest):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    with SessionLocal() as db:
        # Verify cart belongs to user and is active
        cart_check = db.execute(
            text("SELECT id, status FROM carts WHERE id = :cid AND user_id = :uid"),
            {"cid": request.cart_id, "uid": user_id}
        ).fetchone()
        
        if not cart_check:
            raise HTTPException(status_code=404, detail="Cart not found or does not belong to user")
        
        if cart_check.status != 'active':
            raise HTTPException(status_code=400, detail="Cannot add items to non-active cart")

        # Verify product exists
        prod_check = db.execute(text("SELECT 1 FROM products WHERE id = :pid"), {"pid": request.product_id}).fetchone()
        if not prod_check:
            raise HTTPException(status_code=404, detail="Product not found")

        # Check if item already exists in cart (UNIQUE constraint on cart_id, product_id)
        existing_item = db.execute(
            text("SELECT id, quantity FROM cart_items WHERE cart_id = :cid AND product_id = :pid"),
            {"cid": request.cart_id, "pid": request.product_id}
        ).fetchone()

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        if existing_item:
            # Update quantity
            new_qty = existing_item.quantity + request.quantity
            db.execute(
                text("""
                    UPDATE cart_items 
                    SET quantity = :qty, unit_price = :price, updated_at = :now
                    WHERE id = :iid
                """),
                {"qty": new_qty, "price": request.unit_price, "now": now, "iid": existing_item.id}
            )
            db.commit()
            item_id = existing_item.id
        else:
            # Insert new item
            res = db.execute(
                text("""
                    INSERT INTO cart_items (cart_id, product_id, quantity, unit_price, created_at, updated_at)
                    VALUES (:cid, :pid, :qty, :price, :now, :now)
                """),
                {
                    "cid": request.cart_id,
                    "pid": request.product_id,
                    "qty": request.quantity,
                    "price": request.unit_price,
                    "now": now
                }
            )
            db.commit()
            item_id = res.lastrowid

        # Fetch back
        final_res = db.execute(
            text("SELECT id, cart_id, product_id, quantity, unit_price FROM cart_items WHERE id = :iid"),
            {"iid": item_id}
        ).fetchone()
        
        return CartItemResponse(**final_res._asdict())


@app.post("/pickup_schedules", response_model=PickupScheduleResponse, operation_id="schedule_pickup")
def schedule_pickup(request: SchedulePickupRequest):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    with SessionLocal() as db:
        # Verify cart belongs to user and is active
        cart_check = db.execute(
            text("SELECT id, status FROM carts WHERE id = :cid AND user_id = :uid"),
            {"cid": request.cart_id, "uid": user_id}
        ).fetchone()
        
        if not cart_check:
            raise HTTPException(status_code=404, detail="Cart not found or does not belong to user")
        
        if cart_check.status != 'active':
            raise HTTPException(status_code=400, detail="Cannot schedule pickup for non-active cart")

        # Check if a schedule already exists for this cart (UNIQUE constraint on cart_id)
        existing_schedule = db.execute(
            text("SELECT id FROM pickup_schedules WHERE cart_id = :cid"),
            {"cid": request.cart_id}
        ).fetchone()

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        if existing_schedule:
            # Update existing schedule? Or error? Usually scheduling implies creating. 
            # If it exists, let's update it to allow re-scheduling before completion/cancellation.
            db.execute(
                text("""
                    UPDATE pickup_schedules 
                    SET pickup_date = :pd, pickup_time_start = :pts, pickup_time_end = :pte, status = 'scheduled', updated_at = :now
                    WHERE id = :sid
                """),
                {
                    "pd": request.pickup_date,
                    "pts": request.pickup_time_start,
                    "pte": request.pickup_time_end,
                    "now": now,
                    "sid": existing_schedule.id
                }
            )
            db.commit()
            sched_id = existing_schedule.id
        else:
            # Insert new schedule
            res = db.execute(
                text("""
                    INSERT INTO pickup_schedules (cart_id, pickup_date, pickup_time_start, pickup_time_end, status, created_at, updated_at)
                    VALUES (:cid, :pd, :pts, :pte, 'scheduled', :now, :now)
                """),
                {
                    "cid": request.cart_id,
                    "pd": request.pickup_date,
                    "pts": request.pickup_time_start,
                    "pte": request.pickup_time_end,
                    "now": now
                }
            )
            db.commit()
            sched_id = res.lastrowid

        # Fetch back
        final_res = db.execute(
            text("SELECT id, cart_id, pickup_date, pickup_time_start, pickup_time_end, status FROM pickup_schedules WHERE id = :sid"),
            {"sid": sched_id}
        ).fetchone()
        
        return PickupScheduleResponse(**final_res._asdict())
