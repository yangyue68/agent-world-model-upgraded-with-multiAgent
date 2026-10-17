import os
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models ---

class AccountCreate(BaseModel):
    name: str
    contact_name: str
    phone: str
    email: str
    delivery_address: str
    credit_limit: float


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int
    unit_price: float
    keg_deposit_per_unit: Optional[float] = 0.0


class OrderCreate(BaseModel):
    account_id: int
    created_by: int
    delivery_date: str  # YYYY-MM-DD
    items: list[OrderItemCreate]


class DeliveryCreate(BaseModel):
    order_id: int
    scheduled_date: str  # YYYY-MM-DD
    expected_empty_kegs: int


# --- Endpoints ---

@app.post("/accounts", operation_id="create_distribution_account")
def create_distribution_account(payload: AccountCreate):
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO accounts (name, contact_name, phone, email, delivery_address, credit_limit)
                VALUES (:name, :contact_name, :phone, :email, :delivery_address, :credit_limit)
            """),
            {
                "name": payload.name,
                "contact_name": payload.contact_name,
                "phone": payload.phone,
                "email": payload.email,
                "delivery_address": payload.delivery_address,
                "credit_limit": payload.credit_limit,
            },
        )
        account_id = result.lastrowid

        row = conn.execute(
            text("SELECT id, name, contact_name, phone, email, delivery_address, credit_limit FROM accounts WHERE id = :id"),
            {"id": account_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created account")

    return {
        "id": row.id,
        "name": row.name,
        "contact_name": row.contact_name,
        "phone": row.phone,
        "email": row.email,
        "delivery_address": row.delivery_address,
        "credit_limit": row.credit_limit,
    }


@app.post("/orders", operation_id="create_new_order")
def create_new_order(payload: OrderCreate):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authenticated user ID")

    # Validate that the account exists
    with engine.connect() as conn:
        acc_check = conn.execute(text("SELECT id FROM accounts WHERE id = :aid"), {"aid": payload.account_id}).fetchone()
        if not acc_check:
            raise HTTPException(status_code=404, detail=f"Account {payload.account_id} not found")

        # Validate products exist and get their details for deposit calculation logic if needed later
        # For now we just ensure they exist
        product_ids = [item.product_id for item in payload.items]
        placeholders = ", ".join([f":pid_{i}" for i in range(len(product_ids))])
        params = {f"pid_{i}": pid for i, pid in enumerate(product_ids)}
        
        prod_rows = conn.execute(
            text(f"SELECT id, unit_type FROM products WHERE id IN ({placeholders})"),
            params
        ).fetchall()
        
        existing_pids = {r.id for r in prod_rows}
        missing_pids = set(product_ids) - existing_pids
        if missing_pids:
            raise HTTPException(status_code=404, detail=f"Products not found: {missing_pids}")

    # Create Order
    with engine.begin() as conn:
        # Insert into orders table
        res = conn.execute(
            text("""
                INSERT INTO orders (account_id, created_by, delivery_date, status)
                VALUES (:account_id, :created_by, :delivery_date, 'pending')
            """),
            {
                "account_id": payload.account_id,
                "created_by": current_user_id,
                "delivery_date": payload.delivery_date,
            },
        )
        order_id = res.lastrowid

        # Insert order items
        for item in payload.items:
            conn.execute(
                text("""
                    INSERT INTO order_items (order_id, product_id, quantity, unit_price, keg_deposit_per_unit)
                    VALUES (:order_id, :product_id, :quantity, :unit_price, :keg_deposit_per_unit)
                """),
                {
                    "order_id": order_id,
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "keg_deposit_per_unit": item.keg_deposit_per_unit or 0.0,
                },
            )

        # Fetch created order
        row = conn.execute(
            text("SELECT id, account_id, created_by, order_date, delivery_date, status FROM orders WHERE id = :oid"),
            {"oid": order_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created order")

    return {
        "id": row.id,
        "account_id": row.account_id,
        "created_by": row.created_by,
        "order_date": row.order_date.isoformat(sep=' ') if isinstance(row.order_date, datetime) else str(row.order_date),
        "delivery_date": row.delivery_date.isoformat() if isinstance(row.delivery_date, date) else str(row.delivery_date),
        "status": row.status,
    }


@app.post("/deliveries", operation_id="schedule_delivery")
def schedule_delivery(payload: DeliveryCreate):
    # Validate order exists
    with engine.connect() as conn:
        ord_check = conn.execute(text("SELECT id FROM orders WHERE id = :oid"), {"oid": payload.order_id}).fetchone()
        if not ord_check:
            raise HTTPException(status_code=404, detail=f"Order {payload.order_id} not found")

    with engine.begin() as conn:
        res = conn.execute(
            text("""
                INSERT INTO deliveries (order_id, scheduled_date, expected_empty_kegs, status)
                VALUES (:order_id, :scheduled_date, :expected_empty_kegs, 'scheduled')
            """),
            {
                "order_id": payload.order_id,
                "scheduled_date": payload.scheduled_date,
                "expected_empty_kegs": payload.expected_empty_kegs,
            },
        )
        delivery_id = res.lastrowid

        row = conn.execute(
            text("SELECT id, order_id, scheduled_date, expected_empty_kegs, status FROM deliveries WHERE id = :did"),
            {"did": delivery_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created delivery")

    return {
        "id": row.id,
        "order_id": row.order_id,
        "scheduled_date": row.scheduled_date.isoformat() if isinstance(row.scheduled_date, date) else str(row.scheduled_date),
        "expected_empty_kegs": row.expected_empty_kegs,
        "status": row.status,
    }
