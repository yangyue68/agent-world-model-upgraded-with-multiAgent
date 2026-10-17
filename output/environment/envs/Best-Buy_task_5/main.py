import os
import math
from typing import List, Optional

from fastapi import FastAPI, Query, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ProductSearchResponse(BaseModel):
    id: int
    name: str


class StoreNearbyResponse(BaseModel):
    id: int
    name: str


class FulfillmentOptionResponse(BaseModel):
    option_type: str
    is_available: bool
    estimated_days: Optional[int] = None
    cutoff_time: Optional[str] = None


class SameDayDeliveryEligibleStoreResponse(BaseModel):
    store_id: int


def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat / 2) * math.sin(dLat / 2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dLon / 2) * math.sin(dLon / 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


@app.get("/products/search", response_model=List[ProductSearchResponse], operation_id="search_products")
def search_products(name: str = Query(..., description="Product name")):
    query = text("""
        SELECT id, name
        FROM products
        WHERE name LIKE :name_pattern
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"name_pattern": f"%{name}%"}).fetchall()
    return [ProductSearchResponse(id=row.id, name=row.name) for row in result]


@app.get("/stores/nearby", response_model=List[StoreNearbyResponse], operation_id="find_nearby_stores")
def find_nearby_stores(zip_code: str = Query(..., description="ZIP code to search around")):
    ref_query = text("SELECT latitude, longitude FROM stores WHERE zip_code = :zip LIMIT 1")
    with engine.connect() as conn:
        ref_row = conn.execute(ref_query, {"zip": zip_code}).fetchone()

    if not ref_row:
        return []

    ref_lat, ref_lon = ref_row.latitude, ref_row.longitude

    query = text("""
        SELECT id, name, latitude, longitude
        FROM stores
    """)
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    nearby_stores = []
    for row in rows:
        dist = haversine(ref_lat, ref_lon, row.latitude, row.longitude)
        if dist <= 15.0:
            nearby_stores.append(StoreNearbyResponse(id=row.id, name=row.name))

    return nearby_stores


@app.get("/stores/{store_id}/fulfillment", response_model=List[FulfillmentOptionResponse], operation_id="get_store_fulfillment_options")
def get_store_fulfillment_options(store_id: int = Path(..., description="Store ID"), product_id: int = Query(..., description="Product ID")):
    query = text("""
        SELECT option_type, is_available, estimated_days, cutoff_time
        FROM fulfillment_options
        WHERE store_id = :store_id AND product_id = :product_id
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"store_id": store_id, "product_id": product_id}).fetchall()

    results = []
    for row in rows:
        results.append(FulfillmentOptionResponse(
            option_type=row.option_type,
            is_available=bool(row.is_available),
            estimated_days=row.estimated_days,
            cutoff_time=row.cutoff_time
        ))
    return results


@app.get("/stores/same_day_delivery_eligible", response_model=List[SameDayDeliveryEligibleStoreResponse], operation_id="find_same_day_delivery_eligible_stores")
def find_same_day_delivery_eligible_stores(product_id: int = Query(..., description="Product ID"), zip_code: str = Query(..., description="ZIP code to filter stores")):
    ref_query = text("SELECT latitude, longitude FROM stores WHERE zip_code = :zip LIMIT 1")
    with engine.connect() as conn:
        ref_row = conn.execute(ref_query, {"zip": zip_code}).fetchone()

    if not ref_row:
        return []

    ref_lat, ref_lon = ref_row.latitude, ref_row.longitude

    stores_query = text("SELECT id, latitude, longitude FROM stores")
    with engine.connect() as conn:
        all_stores = conn.execute(stores_query).fetchall()

    nearby_store_ids = []
    for store in all_stores:
        dist = haversine(ref_lat, ref_lon, store.latitude, store.longitude)
        if dist <= 15.0:
            nearby_store_ids.append(store.id)

    if not nearby_store_ids:
        return []

    placeholders = ",".join([f":sid_{i}" for i in range(len(nearby_store_ids))])
    params = {f"sid_{i}": sid for i, sid in enumerate(nearby_store_ids)}
    params["product_id"] = product_id

    query = text(f"""
        SELECT DISTINCT store_id
        FROM fulfillment_options
        WHERE product_id = :product_id
          AND option_type = 'same_day_delivery'
          AND is_available = 1
          AND store_id IN ({placeholders})
    """)

    with engine.connect() as conn:
        rows = conn.execute(query, params).fetchall()

    return [SameDayDeliveryEligibleStoreResponse(store_id=row.store_id) for row in rows]
