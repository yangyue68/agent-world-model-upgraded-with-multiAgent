import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import Session, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine)

app = FastAPI(title="Charles Schwab API", version="1.0.0")


# --- Pydantic Models ---

class PlaceLimitOrderRequest(BaseModel):
    account_id: int = Field(..., description="Internal ID of the account to place the order from")
    security_symbol: str = Field(..., description="Stock symbol of the security to trade (e.g., AAPL)")
    quantity: int = Field(..., gt=0, description="Number of shares to buy")
    limit_price: float = Field(..., gt=0.0, description="Maximum price to pay per share")
    time_in_force: str = Field(..., description="Order duration: Day, GTC, IOC, FOK, GTD")
    trading_platform: str = Field(..., description="Platform used to place the order: thinkorswim, schwab, web, mobile")


class OrderResponse(BaseModel):
    id: int
    account_id: int
    security_id: int
    order_type: str
    side: str
    quantity: int
    limit_price: float
    time_in_force: str
    trading_platform: str
    status: str
    created_at: str

    class Config:
        from_attributes = True


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def validate_enum_value(value: str, valid_values: list[str], field_name: str):
    if value not in valid_values:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid value for {field_name}: '{value}'. Must be one of {valid_values}"
        )


# --- Endpoints ---

@app.post("/orders", response_model=OrderResponse, operation_id="place_limit_order")
def place_limit_order(order_data: PlaceLimitOrderRequest):
    """Place a limit order to buy securities"""
    
    # Validate Enums based on Schema
    valid_tif = ['Day', 'GTC', 'IOC', 'FOK', 'GTD']
    validate_enum_value(order_data.time_in_force, valid_tif, "time_in_force")
    
    valid_platforms = ['thinkorswim', 'schwab', 'web', 'mobile']
    validate_enum_value(order_data.trading_platform, valid_platforms, "trading_platform")

    db = SessionLocal()
    try:
        # 1. Verify Account exists and belongs to current user
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")

        account_check = db.execute(
            text("SELECT id FROM accounts WHERE id = :account_id AND user_id = :user_id"),
            {"account_id": order_data.account_id, "user_id": current_user_id}
        ).fetchone()

        if not account_check:
            raise HTTPException(status_code=404, detail="Account not found or access denied")

        # 2. Verify Security exists
        security_check = db.execute(
            text("SELECT id FROM securities WHERE symbol = :symbol"),
            {"symbol": order_data.security_symbol}
        ).fetchone()

        if not security_check:
            raise HTTPException(status_code=404, detail="Security not found")

        security_id = security_check[0]

        # 3. Insert Order
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_sql = text("""
            INSERT INTO orders 
            (account_id, security_id, order_type, side, quantity, limit_price, time_in_force, trading_platform, status, filled_quantity, average_fill_price, created_at, updated_at)
            VALUES 
            (:account_id, :security_id, :order_type, :side, :quantity, :limit_price, :time_in_force, :trading_platform, :status, :filled_quantity, :average_fill_price, :created_at, :updated_at)
        """)

        db.execute(insert_sql, {
            "account_id": order_data.account_id,
            "security_id": security_id,
            "order_type": "limit",
            "side": "buy",
            "quantity": order_data.quantity,
            "limit_price": order_data.limit_price,
            "time_in_force": order_data.time_in_force,
            "trading_platform": order_data.trading_platform,
            "status": "pending",
            "filled_quantity": 0,
            "average_fill_price": None,
            "created_at": now,
            "updated_at": now
        })
        
        db.commit()

        # 4. Retrieve the newly created order to return it
        last_id_sql = text("SELECT last_insert_rowid()")
        new_order_id = db.execute(last_id_sql).fetchone()[0]

        order_fetch = db.execute(
            text("SELECT * FROM orders WHERE id = :id"),
            {"id": new_order_id}
        ).fetchone()

        if not order_fetch:
            raise HTTPException(status_code=500, detail="Failed to retrieve created order")

        # Map columns to response model
        columns = [desc[0] for desc in db.execute(text("SELECT * FROM orders LIMIT 0")).cursor.description]
        order_dict = dict(zip(columns, order_fetch))
        
        # Format created_at as ISO string for consistency if needed, though DB stores standard format
        # The spec says format date-time. SQLite DATETIME usually returns YYYY-MM-DD HH:MM:SS
        # We will return it as is or ensure it matches expected output format.
        # Standard practice for these tasks is often just returning the stored string.
        
        return OrderResponse(
            id=order_dict['id'],
            account_id=order_dict['account_id'],
            security_id=order_dict['security_id'],
            order_type=order_dict['order_type'],
            side=order_dict['side'],
            quantity=order_dict['quantity'],
            limit_price=order_dict['limit_price'],
            time_in_force=order_dict['time_in_force'],
            trading_platform=order_dict['trading_platform'],
            status=order_dict['status'],
            created_at=order_dict['created_at']
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
