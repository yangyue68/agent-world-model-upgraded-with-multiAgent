import os
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ExpiringItemResponse(BaseModel):
    id: int
    name: str
    sku: str
    expiration_date: str
    status: str


class MarkForRemovalResponse(BaseModel):
    id: int
    name: str
    status: str
    marked_for_removal_at: str


@app.get("/inventory/expiring_soon", response_model=list[ExpiringItemResponse], operation_id="find_expiring_inventory")
def find_expiring_inventory():
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_date = (now_utc + timedelta(days=30)).strftime("%Y-%m-%d")

    query = text("""
        SELECT id, name, sku, expiration_date, status
        FROM inventory_items
        WHERE expiration_date <= :cutoff_date
          AND status != 'removed'
        ORDER BY expiration_date ASC
    """)

    with engine.connect() as conn:
        rows = conn.execute(query, {"cutoff_date": cutoff_date}).fetchall()

    return [
        {
            "id": row.id,
            "name": row.name,
            "sku": row.sku,
            "expiration_date": row.expiration_date,
            "status": row.status,
        }
        for row in rows
    ]


@app.put("/inventory/{id}/mark_for_removal", response_model=MarkForRemovalResponse, operation_id="mark_item_for_removal")
def mark_item_for_removal(id: int):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    # Verify user exists to satisfy FK constraint implicitly or explicitly
    check_user_query = text("SELECT id FROM users WHERE id = :uid")
    with engine.connect() as conn:
        user_exists = conn.execute(check_user_query, {"uid": user_id}).fetchone()
        if not user_exists:
            raise HTTPException(status_code=401, detail="User not found")

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    timestamp_str = now_utc.strftime("%Y-%m-%d %H:%M:%S")

    update_query = text("""
        UPDATE inventory_items
        SET status = 'marked_for_removal',
            marked_for_removal_at = :ts,
            marked_for_removal_by = :uid,
            updated_at = :ts
        WHERE id = :iid
          AND status != 'removed'
    """)

    select_query = text("""
        SELECT id, name, status, marked_for_removal_at
        FROM inventory_items
        WHERE id = :iid
    """)

    with engine.begin() as conn:
        result = conn.execute(update_query, {"ts": timestamp_str, "uid": user_id, "iid": id})
        
        if result.rowcount == 0:
            # Check if item exists but was already removed or doesn't exist
            existing = conn.execute(select_query, {"iid": id}).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Inventory item not found")
            else:
                raise HTTPException(status_code=409, detail="Item is already removed or cannot be marked")

        final_row = conn.execute(select_query, {"iid": id}).fetchone()

    return {
        "id": final_row.id,
        "name": final_row.name,
        "status": final_row.status,
        "marked_for_removal_at": final_row.marked_for_removal_at,
    }
