import os
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class StoreInfo(BaseModel):
    id: int
    name: str
    address: str


class ProductInfo(BaseModel):
    id: int
    name: str
    model_number: str


class InventoryInfo(BaseModel):
    quantity_on_hand: int
    quantity_reserved: int


class DeliveryOption(BaseModel):
    type: str
    available: bool
    cutoff_time: Optional[str] = None
    estimated_delivery_start: Optional[str] = None
    estimated_delivery_end: Optional[str] = None
    fee: Optional[float] = None


class PickupOption(BaseModel):
    available: bool
    estimated_pickup_start: Optional[str] = None
    estimated_pickup_end: Optional[str] = None


class TimeWindow(BaseModel):
    window_start: str
    window_end: str


class AvailabilityResponse(BaseModel):
    store: StoreInfo
    product: ProductInfo
    inventory: InventoryInfo
    delivery_options: List[DeliveryOption]
    pickup_options: List[PickupOption]
    earliest_same_day_delivery_window: TimeWindow
    earliest_pickup_window: TimeWindow


def format_datetime(dt_str: Optional[str]) -> Optional[str]:
    if dt_str is None:
        return None
    # Normalize to ISO 8601 with T separator and Z suffix if needed
    # SQLite stores as 'YYYY-MM-DD HH:MM:SS'
    if "T" in dt_str:
        return dt_str
    return dt_str.replace(" ", "T") + "Z"


@app.get(
    "/availability",
    operation_id="check_availability_and_delivery_options",
    response_model=AvailabilityResponse,
)
def check_availability_and_delivery_options(
    store_address: str = Query(..., description="Full address of the store"),
    product_model: str = Query(..., description="Model number of the product"),
):
    """
    Check real-time store availability and delivery options for a specific product at a given store.
    """
    # Parse store address to find the store
    # The spec says store_address is like '12345 SW 1st St, Miami, FL 33174'
    # We need to match against stores table which has address_line1, city, state, zip_code
    
    # Try to parse the address components
    parts = [p.strip() for p in store_address.split(",")]
    
    # Build query to find matching store
    # We'll try to match by combining address_line1, city, state, zip_code
    with engine.connect() as conn:
        # First, try to find store by exact address match pattern
        # The input is "12345 SW 1st St, Miami, FL 33174"
        # Store has: address_line1="12345 SW 1st St", city="Miami", state="FL", zip_code="33174"
        
        # Let's construct a LIKE-based search or direct matching
        # For simplicity, we'll try to match each component
        
        addr_line1 = parts[0] if len(parts) > 0 else ""
        city = parts[1] if len(parts) > 1 else ""
        state_zip = parts[2] if len(parts) > 2 else ""
        
        # Split state and zip from last part
        state = ""
        zip_code = ""
        if state_zip:
            sz_parts = state_zip.split()
            if len(sz_parts) >= 2:
                state = sz_parts[0]
                zip_code = sz_parts[-1]
        
        # Find the store
        store_query = text("""
            SELECT id, name, address_line1, city, state, zip_code
            FROM stores
            WHERE address_line1 = :addr_line1
              AND city = :city
              AND state = :state
              AND zip_code = :zip_code
        """)
        
        store_row = conn.execute(store_query, {
            "addr_line1": addr_line1,
            "city": city,
            "state": state,
            "zip_code": zip_code,
        }).fetchone()
        
        if not store_row:
            # Try alternative matching - maybe the full address string matches differently
            # Let's try a more flexible approach
            store_query_flex = text("""
                SELECT id, name, address_line1, city, state, zip_code
                FROM stores
                WHERE (address_line1 || ', ' || city || ', ' || state || ' ' || zip_code) = :full_addr
                   OR (address_line1 || ', ' || city || ', ' || state || ' ' || zip_code) LIKE :pattern
            """)
            
            full_addr = f"{addr_line1}, {city}, {state} {zip_code}"
            pattern = f"%{addr_line1}%{city}%{state}%{zip_code}%"
            
            store_row = conn.execute(store_query_flex, {
                "full_addr": full_addr,
                "pattern": pattern,
            }).fetchone()
        
        if not store_row:
            raise HTTPException(status_code=404, detail="Store not found")
        
        store_id = store_row[0]
        store_name = store_row[1]
        store_addr_line1 = store_row[2]
        store_city = store_row[3]
        store_state = store_row[4]
        store_zip = store_row[5]
        
        # Construct full address for response
        full_store_address = f"{store_addr_line1}, {store_city}, {store_state} {store_zip}"
        
        # Find the product by model number
        product_query = text("""
            SELECT id, name, model_number
            FROM products
            WHERE model_number = :model_number
        """)
        
        product_row = conn.execute(product_query, {"model_number": product_model}).fetchone()
        
        if not product_row:
            raise HTTPException(status_code=404, detail="Product not found")
        
        product_id = product_row[0]
        product_name = product_row[1]
        product_model_number = product_row[2]
        
        # Get inventory
        inventory_query = text("""
            SELECT quantity_on_hand, quantity_reserved
            FROM store_inventory
            WHERE store_id = :store_id AND product_id = :product_id
        """)
        
        inventory_row = conn.execute(inventory_query, {
            "store_id": store_id,
            "product_id": product_id,
        }).fetchone()
        
        if inventory_row:
            qty_on_hand = inventory_row[0]
            qty_reserved = inventory_row[1]
        else:
            qty_on_hand = 0
            qty_reserved = 0
        
        # Get delivery options
        delivery_query = text("""
            SELECT delivery_type, available, cutoff_time, 
                   estimated_delivery_start, estimated_delivery_end, fee
            FROM delivery_options
            WHERE store_id = :store_id AND product_id = :product_id
        """)
        
        delivery_rows = conn.execute(delivery_query, {
            "store_id": store_id,
            "product_id": product_id,
        }).fetchall()
        
        delivery_options_list = []
        for row in delivery_rows:
            delivery_options_list.append(DeliveryOption(
                type=row[0],
                available=bool(row[1]),
                cutoff_time=row[2],
                estimated_delivery_start=format_datetime(row[3]),
                estimated_delivery_end=format_datetime(row[4]),
                fee=row[5],
            ))
        
        # Get pickup options
        pickup_query = text("""
            SELECT available, estimated_pickup_start, estimated_pickup_end
            FROM pickup_options
            WHERE store_id = :store_id AND product_id = :product_id
        """)
        
        pickup_rows = conn.execute(pickup_query, {
            "store_id": store_id,
            "product_id": product_id,
        }).fetchall()
        
        pickup_options_list = []
        for row in pickup_rows:
            pickup_options_list.append(PickupOption(
                available=bool(row[0]),
                estimated_pickup_start=format_datetime(row[1]),
                estimated_pickup_end=format_datetime(row[2]),
            ))
        
        # Find earliest same-day delivery window
        # Look for delivery_option with type='same_day' that is available, then find its earliest window
        earliest_same_day_window = None
        
        same_day_delivery_ids = [
            row[0] for row in conn.execute(text("""
                SELECT do.id
                FROM delivery_options do
                WHERE do.store_id = :store_id 
                  AND do.product_id = :product_id 
                  AND do.delivery_type = 'same_day'
                  AND do.available = 1
            """), {"store_id": store_id, "product_id": product_id}).fetchall()
        ]
        
        if same_day_delivery_ids:
            # Find earliest available window among these delivery options
            placeholders = ",".join([f":did_{i}" for i in range(len(same_day_delivery_ids))])
            params = {f"did_{i}": did for i, did in enumerate(same_day_delivery_ids)}
            
            window_query = text(f"""
                SELECT dw.window_start, dw.window_end
                FROM delivery_windows dw
                WHERE dw.delivery_option_id IN ({placeholders})
                  AND dw.available = 1
                  AND dw.booked < dw.capacity
                ORDER BY dw.window_start ASC
                LIMIT 1
            """)
            
            window_row = conn.execute(window_query, params).fetchone()
            
            if window_row:
                earliest_same_day_window = TimeWindow(
                    window_start=format_datetime(window_row[0]),
                    window_end=format_datetime(window_row[1]),
                )
        
        # If no windows found but there are same-day delivery options, use their estimated times
        if earliest_same_day_window is None and same_day_delivery_ids:
            # Fall back to the first same-day option's estimated times
            fallback_query = text("""
                SELECT estimated_delivery_start, estimated_delivery_end
                FROM delivery_options
                WHERE id = :did
            """)
            fb_row = conn.execute(fallback_query, {"did": same_day_delivery_ids[0]}).fetchone()
            if fb_row and fb_row[0] and fb_row[1]:
                earliest_same_day_window = TimeWindow(
                    window_start=format_datetime(fb_row[0]),
                    window_end=format_datetime(fb_row[1]),
                )
        
        # If still none, provide a default empty window structure won't work since required
        # Let's ensure we have something - if truly no same-day available, this might be an issue
        # But per spec, earliest_same_day_delivery_window is required
        if earliest_same_day_window is None:
            # Return a placeholder indicating no availability? Spec requires object with start/end
            # Let's check if there's any same_day option at all even if unavailable
            any_same_day = conn.execute(text("""
                SELECT estimated_delivery_start, estimated_delivery_end
                FROM delivery_options
                WHERE store_id = :store_id 
                  AND product_id = :product_id 
                  AND delivery_type = 'same_day'
                LIMIT 1
            """), {"store_id": store_id, "product_id": product_id}).fetchone()
            
            if any_same_day and any_same_day[0] and any_same_day[1]:
                earliest_same_day_window = TimeWindow(
                    window_start=format_datetime(any_same_day[0]),
                    window_end=format_datetime(any_same_day[1]),
                )
            else:
                # Last resort: use current time-ish placeholder - but better to raise or return null-like
                # Since it's required, let's just pick the first delivery option's times if exists
                raise HTTPException(status_code=404, detail="No same-day delivery window available")
        
        # Find earliest pickup window
        earliest_pickup_window = None
        
        pickup_option_ids = [
            row[0] for row in conn.execute(text("""
                SELECT po.id
                FROM pickup_options po
                WHERE po.store_id = :store_id 
                  AND po.product_id = :product_id 
                  AND po.available = 1
            """), {"store_id": store_id, "product_id": product_id}).fetchall()
        ]
        
        if pickup_option_ids:
            placeholders = ",".join([f":pid_{i}" for i in range(len(pickup_option_ids))])
            params = {f"pid_{i}": pid for i, pid in enumerate(pickup_option_ids)}
            
            pw_query = text(f"""
                SELECT pw.window_start, pw.window_end
                FROM pickup_windows pw
                WHERE pw.pickup_option_id IN ({placeholders})
                  AND pw.available = 1
                  AND pw.booked < pw.capacity
                ORDER BY pw.window_start ASC
                LIMIT 1
            """)
            
            pw_row = conn.execute(pw_query, params).fetchone()
            
            if pw_row:
                earliest_pickup_window = TimeWindow(
                    window_start=format_datetime(pw_row[0]),
                    window_end=format_datetime(pw_row[1]),
                )
        
        # Fallback to pickup option's estimated times if no windows
        if earliest_pickup_window is None and pickup_option_ids:
            fb_pu_query = text("""
                SELECT estimated_pickup_start, estimated_pickup_end
                FROM pickup_options
                WHERE id = :pid
            """)
            fb_pu_row = conn.execute(fb_pu_query, {"pid": pickup_option_ids[0]}).fetchone()
            if fb_pu_row and fb_pu_row[0] and fb_pu_row[1]:
                earliest_pickup_window = TimeWindow(
                    window_start=format_datetime(fb_pu_row[0]),
                    window_end=format_datetime(fb_pu_row[1]),
                )
        
        if earliest_pickup_window is None:
            # Check if any pickup option exists at all
            any_pu = conn.execute(text("""
                SELECT estimated_pickup_start, estimated_pickup_end
                FROM pickup_options
                WHERE store_id = :store_id 
                  AND product_id = :product_id
                LIMIT 1
            """), {"store_id": store_id, "product_id": product_id}).fetchone()
            
            if any_pu and any_pu[0] and any_pu[1]:
                earliest_pickup_window = TimeWindow(
                    window_start=format_datetime(any_pu[0]),
                    window_end=format_datetime(any_pu[1]),
                )
            else:
                raise HTTPException(status_code=404, detail="No pickup window available")
        
        # Build response
        store_info = StoreInfo(
            id=store_id,
            name=store_name,
            address=full_store_address,
        )
        
        product_info = ProductInfo(
            id=product_id,
            name=product_name,
            model_number=product_model_number,
        )
        
        inventory_info = InventoryInfo(
            quantity_on_hand=qty_on_hand,
            quantity_reserved=qty_reserved,
        )
        
        return AvailabilityResponse(
            store=store_info,
            product=product_info,
            inventory=inventory_info,
            delivery_options=delivery_options_list,
            pickup_options=pickup_options_list,
            earliest_same_day_delivery_window=earliest_same_day_window,
            earliest_pickup_window=earliest_pickup_window,
        )
