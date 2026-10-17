import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_URL = os.environ.get("DATABASE_PATH")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CVS Pharmacy API")


# --- Pydantic Models for Request/Response ---

class CreateOrderRequest(BaseModel):
    product_sku: str
    address_id: int
    preferred_start_time: str  # HH:MM format
    preferred_end_time: str    # HH:MM format


class DeliverySlotInfo(BaseModel):
    start_time: str
    end_time: str


class OrderResponse(BaseModel):
    order_id: int
    status: str
    delivery_slot: Optional[DeliverySlotInfo] = None


class ProductResponse(BaseModel):
    id: int
    sku: str
    name: str
    brand: str
    price: float


class AddressResponse(BaseModel):
    id: int
    user_id: int
    street: str
    city: str
    state: str
    zip: str


class SlotResponse(BaseModel):
    id: int
    start_time: str
    end_time: str
    capacity: int


# --- Helper Functions ---

def get_current_user_id() -> int:
    """Reads the current user ID from the environment."""
    uid_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not uid_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(uid_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


def parse_hhmm(time_str: str) -> tuple[int, int]:
    """Parses HH:MM string into hours and minutes integers."""
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: {time_str}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"Time out of range: {time_str}")
    return h, m


def time_to_minutes(hh_mm: str) -> int:
    """Converts HH:MM to total minutes since midnight."""
    h, m = parse_hhmm(hh_mm)
    return h * 60 + m


# --- Endpoints ---

@app.post("/orders", response_model=OrderResponse, operation_id="create_same_day_delivery_order")
def create_same_day_delivery_order(request: CreateOrderRequest):
    user_id = get_current_user_id()
    
    with SessionLocal() as db:
        # 1. Validate Product exists and is active
        prod_res = db.execute(text("""
            SELECT id, sku, name, price 
            FROM products 
            WHERE sku = :sku AND is_active = 1
        """), {"sku": request.product_sku})
        product_row = prod_res.fetchone()
        
        if not product_row:
            raise HTTPException(status_code=404, detail=f"Product with SKU {request.product_sku} not found or inactive.")
        
        product_id = product_row.id
        product_price = product_row.price
        
        # 2. Validate Address belongs to user
        addr_res = db.execute(text("""
            SELECT id, user_id, street, city, state, zip
            FROM addresses
            WHERE id = :addr_id
        """), {"addr_id": request.address_id})
        address_row = addr_res.fetchone()
        
        if not address_row:
            raise HTTPException(status_code=404, detail="Address not found.")
        
        if address_row.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied: Address does not belong to current user.")
            
        # Determine Store based on Address Zip/City? 
        # The spec doesn't explicitly say how to pick a store, but typically it's the nearest one.
        # Looking at data: User 1 has address in Boston 02101. Stores are in Boston (02116, 02199) and Cambridge (02138).
        # We need to find a store that serves this area. For simplicity in this scenario without geo-distance logic,
        # we might assume the first available store with inventory and slots, or perhaps there's a mapping.
        # However, looking at existing orders:
        # Order 1: User 2 (Addr 2 - Boston 02108) -> Store 2 (Back Bay 02199)
        # Order 2: User 3 (Addr 3 - Boston 02116) -> Store 1 (Downtown 02116)
        # Order 3: User 4 (Addr 4 - Brookline 02446) -> Store 3 (Cambridge 02138) -- This seems odd geographically but let's follow DB patterns.
        
        # Let's find stores that have the product in stock.
        inv_res = db.execute(text("""
            SELECT i.store_id, s.name as store_name, s.zip as store_zip
            FROM inventory i
            JOIN stores s ON i.store_id = s.id
            WHERE i.product_id = :prod_id AND i.quantity > 0
        """), {"prod_id": product_id})
        
        available_stores = inv_res.fetchall()
        if not available_stores:
            raise HTTPException(status_code=400, detail="No stores currently have this product in stock.")
        
        # Heuristic for picking store: In real life, closest. Here, let's just pick the first one that has an available slot matching criteria.
        # Or better, iterate through available stores to find a valid slot.
        
        today_str = date.today().isoformat()
        
        chosen_store_id = None
        chosen_slot_id = None
        chosen_slot_start = None
        chosen_slot_end = None
        
        req_start_min = time_to_minutes(request.preferred_start_time)
        req_end_min = time_to_minutes(request.preferred_end_time)
        
        if req_start_min >= req_end_min:
             raise HTTPException(status_code=400, detail="Preferred start time must be before end time.")

        for store_info in available_stores:
            sid = store_info.store_id
            
            # Find active slots for this store today that overlap with preferred window
            # Overlap condition: slot.start < pref.end AND slot.end > pref.start
            slot_res = db.execute(text("""
                SELECT id, start_time, end_time, capacity
                FROM delivery_slots
                WHERE store_id = :sid 
                  AND slot_date = :today
                  AND is_active = 1
                  AND capacity > 0
                  AND start_time < :pref_end
                  AND end_time > :pref_start
                ORDER BY start_time ASC
            """), {
                "sid": sid,
                "today": today_str,
                "pref_start": request.preferred_start_time,
                "pref_end": request.preferred_end_time
            })
            
            slot_row = slot_res.fetchone()
            if slot_row:
                chosen_store_id = sid
                chosen_slot_id = slot_row.id
                chosen_slot_start = slot_row.start_time
                chosen_slot_end = slot_row.end_time
                break # Found a suitable store/slot combination
        
        if not chosen_store_id:
            raise HTTPException(status_code=400, detail="No available delivery slots found for the requested time window.")
        
        # 3. Create Order
        now_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
        insert_order_res = db.execute(text("""
            INSERT INTO orders (user_id, address_id, store_id, delivery_slot_id, status, total_amount, created_at, updated_at)
            VALUES (:uid, :aid, :sid, :dsid, 'pending', :amount, :now, :now)
        """), {
            "uid": user_id,
            "aid": request.address_id,
            "sid": chosen_store_id,
            "dsid": chosen_slot_id,
            "amount": product_price, # Assuming qty 1 for single item order creation via this endpoint context
            "now": now_ts
        })
        
        order_id = insert_order_res.lastrowid
        
        # 4. Create Order Item
        db.execute(text("""
            INSERT INTO order_items (order_id, product_id, quantity, unit_price, created_at)
            VALUES (:oid, :pid, 1, :uprice, :now)
        """), {
            "oid": order_id,
            "pid": product_id,
            "uprice": product_price,
            "now": now_ts
        })
        
        # 5. Decrement Inventory
        db.execute(text("""
            UPDATE inventory
            SET quantity = quantity - 1, updated_at = :now
            WHERE product_id = :pid AND store_id = :sid
        """), {
            "pid": product_id,
            "sid": chosen_store_id,
            "now": now_ts
        })
        
        # 6. Update Slot Capacity (Optional depending on business logic, usually reserved slots reduce capacity)
        db.execute(text("""
            UPDATE delivery_slots
            SET capacity = capacity - 1, updated_at = :now
            WHERE id = :slot_id
        """), {
            "slot_id": chosen_slot_id,
            "now": now_ts
        })
        
        db.commit()
        
        return OrderResponse(
            order_id=order_id,
            status="pending",
            delivery_slot=DeliverySlotInfo(start_time=chosen_slot_start, end_time=chosen_slot_end)
        )


@app.get("/products/{sku}", response_model=ProductResponse, operation_id="get_product_by_sku")
def get_product_by_sku(sku: str):
    with SessionLocal() as db:
        res = db.execute(text("""
            SELECT id, sku, name, brand, price
            FROM products
            WHERE sku = :sku
        """), {"sku": sku})
        row = res.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")
            
        return ProductResponse(
            id=row.id,
            sku=row.sku,
            name=row.name,
            brand=row.brand,
            price=row.price
        )


@app.get("/addresses/{id}", response_model=AddressResponse, operation_id="get_address_by_id")
def get_address_by_id(id: int):
    user_id = get_current_user_id()
    
    with SessionLocal() as db:
        res = db.execute(text("""
            SELECT id, user_id, street, city, state, zip
            FROM addresses
            WHERE id = :id
        """), {"id": id})
        row = res.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Address not found")
            
        if row.user_id != user_id:
            raise HTTPException(status_code=403, detail="Forbidden: You do not own this address")
            
        return AddressResponse(
            id=row.id,
            user_id=row.user_id,
            street=row.street,
            city=row.city,
            state=row.state,
            zip=row.zip
        )


@app.get("/delivery-slots/available", response_model=list[SlotResponse], operation_id="find_available_delivery_slots")
def find_available_delivery_slots(
    store_id: int = Query(...),
    delivery_date: str = Query(...),
    preferred_start_time: str = Query(...),
    preferred_end_time: str = Query(...)
):
    # Validate times
    try:
        ps_min = time_to_minutes(preferred_start_time)
        pe_min = time_to_minutes(preferred_end_time)
        if ps_min >= pe_min:
            raise ValueError("Start time must be before end time")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    with SessionLocal() as db:
        # Find slots that overlap with the preferred window
        # Overlap: slot.start < pref.end AND slot.end > pref.start
        res = db.execute(text("""
            SELECT id, start_time, end_time, capacity
            FROM delivery_slots
            WHERE store_id = :sid
              AND slot_date = :date
              AND is_active = 1
              AND capacity > 0
              AND start_time < :p_end
              AND end_time > :p_start
            ORDER BY start_time ASC
        """), {
            "sid": store_id,
            "date": delivery_date,
            "p_start": preferred_start_time,
            "p_end": preferred_end_time
        })
        
        rows = res.fetchall()
        
        return [
            SlotResponse(
                id=r.id,
                start_time=r.start_time,
                end_time=r.end_time,
                capacity=r.capacity
            ) for r in rows
        ]
