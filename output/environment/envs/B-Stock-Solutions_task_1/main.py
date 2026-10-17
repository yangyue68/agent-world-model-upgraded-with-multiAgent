import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

# The database path is provided via environment variable or constant as per instructions
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()

class BidRequest(BaseModel):
    amount: float = Field(..., description="The bid amount to place")

class AutoBidRequest(BaseModel):
    max_bid: float = Field(..., description="The maximum bid amount for the auto-bid rule")

class LotResponse(BaseModel):
    id: str
    current_bid: float
    bid_count: int

class PlaceBidResponse(BaseModel):
    success: bool
    message: str
    lot: Optional[LotResponse] = None

class AutoBidResponseData(BaseModel):
    id: int
    lot_id: str
    user_id: int
    max_bid: float
    is_active: int

class SetAutoBidResponse(BaseModel):
    success: bool
    message: str
    auto_bid: Optional[AutoBidResponseData] = None


@app.post("/lots/{lot_id}/bids", response_model=PlaceBidResponse, operation_id="place_bid_on_lot")
def place_bid_on_lot(
    lot_id: str = Path(..., description="The unique identifier of the lot to bid on"),
    bid_request: BidRequest = Body(...)
):
    """
    Places a bid of the specified amount on the given lot ID. 
    The bid must be higher than the current bid. 
    This operation updates the lot's current bid and bid count.
    """
    # Get current user ID from environment
    try:
        user_id_str = os.environ["AWM_CURRENT_USER_ID"]
        user_id = int(user_id_str)
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Unauthorized: Missing or invalid user context")

    with engine.connect() as conn:
        # 1. Check if lot exists and get current details
        stmt = text("SELECT id, title, category, location, current_bid, bid_count, status FROM lots WHERE id = :lot_id")
        result = conn.execute(stmt, {"lot_id": lot_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Lot {lot_id} not found")
        
        lot_data = dict(row._mapping)
        current_bid = lot_data['current_bid']
        status = lot_data['status']
        
        # 2. Validate bid amount
        if status != 'active':
            raise HTTPException(status_code=400, detail="Cannot bid on inactive lot")
            
        if bid_request.amount <= current_bid:
            raise HTTPException(status_code=400, detail=f"Bid amount must be higher than current bid ({current_bid})")
        
        # 3. Insert new bid
        insert_bid_stmt = text("""
            INSERT INTO bids (lot_id, user_id, amount, created_at) 
            VALUES (:lot_id, :user_id, :amount, CURRENT_TIMESTAMP)
        """)
        conn.execute(insert_bid_stmt, {
            "lot_id": lot_id,
            "user_id": user_id,
            "amount": bid_request.amount
        })
        
        # 4. Update lot with new current_bid and increment bid_count
        update_lot_stmt = text("""
            UPDATE lots 
            SET current_bid = :new_bid, 
                bid_count = bid_count + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :lot_id
        """)
        conn.execute(update_lot_stmt, {
            "new_bid": bid_request.amount,
            "lot_id": lot_id
        })
        
        conn.commit()
        
        # Fetch updated lot info for response
        updated_result = conn.execute(text("SELECT id, current_bid, bid_count FROM lots WHERE id = :lot_id"), {"lot_id": lot_id})
        updated_row = updated_result.fetchone()
        
        if not updated_row:
             # Should theoretically not happen after successful update
            raise HTTPException(status_code=500, detail="Failed to retrieve updated lot data")
            
        updated_lot_data = dict(updated_row._mapping)
        
        return PlaceBidResponse(
            success=True,
            message="Bid placed successfully",
            lot=LotResponse(
                id=updated_lot_data['id'],
                current_bid=updated_lot_data['current_bid'],
                bid_count=updated_lot_data['bid_count']
            )
        )


@app.post("/lots/{lot_id}/auto_bids", response_model=SetAutoBidResponse, operation_id="set_auto_bid_rule")
def set_auto_bid_rule(
    lot_id: str = Path(..., description="The unique identifier of the lot to set auto-bid rule for"),
    auto_bid_request: AutoBidRequest = Body(...)
):
    """
    Creates or updates an auto-bid rule for the specified lot with a maximum bid amount. 
    The auto-bid will automatically place bids up to this maximum if the lot is active and the user's bid is lower than the maximum.
    """
    # Get current user ID from environment
    try:
        user_id_str = os.environ["AWM_CURRENT_USER_ID"]
        user_id = int(user_id_str)
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Unauthorized: Missing or invalid user context")

    with engine.connect() as conn:
        # 1. Check if lot exists
        stmt = text("SELECT id, status FROM lots WHERE id = :lot_id")
        result = conn.execute(stmt, {"lot_id": lot_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Lot {lot_id} not found")
        
        lot_status = row.status
        
        if lot_status != 'active':
            raise HTTPException(status_code=400, detail="Cannot set auto-bid on inactive lot")

        # 2. Upsert auto_bid rule
        # SQLite supports INSERT OR REPLACE or ON CONFLICT DO UPDATE
        # Using ON CONFLICT for clarity and standard SQL behavior where supported by SQLAlchemy/SQLite dialect
        
        upsert_stmt = text("""
            INSERT INTO auto_bids (lot_id, user_id, max_bid, is_active, created_at, updated_at)
            VALUES (:lot_id, :user_id, :max_bid, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(lot_id, user_id) DO UPDATE SET
                max_bid = excluded.max_bid,
                is_active = 1,
                updated_at = CURRENT_TIMESTAMP
        """)
        
        conn.execute(upsert_stmt, {
            "lot_id": lot_id,
            "user_id": user_id,
            "max_bid": auto_bid_request.max_bid
        })
        
        conn.commit()
        
        # 3. Retrieve the created/updated record
        select_stmt = text("""
            SELECT id, lot_id, user_id, max_bid, is_active 
            FROM auto_bids 
            WHERE lot_id = :lot_id AND user_id = :user_id
        """)
        
        res = conn.execute(select_stmt, {"lot_id": lot_id, "user_id": user_id})
        ab_row = res.fetchone()
        
        if not ab_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve auto-bid rule")
            
        ab_data = dict(ab_row._mapping)
        
        return SetAutoBidResponse(
            success=True,
            message="Auto-bid rule set successfully",
            auto_bid=AutoBidResponseData(
                id=ab_data['id'],
                lot_id=ab_data['lot_id'],
                user_id=ab_data['user_id'],
                max_bid=ab_data['max_bid'],
                is_active=ab_data['is_active']
            )
        )
