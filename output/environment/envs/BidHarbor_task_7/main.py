import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from typing import Optional

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class BidRequest(BaseModel):
    amount: float = Field(..., description="The amount of the bid ($120)")
    max_auto_bid: float = Field(..., description="The maximum amount the system can automatically bid on behalf of the user ($150)")


class BidResponse(BaseModel):
    id: int
    auction_id: int
    user_id: int
    amount: float
    is_proxy: int
    max_auto_bid: float
    created_at: str


@app.post(
    "/auctions/{auction_id}/bids",
    operation_id="place_proxy_bid",
    response_model=BidResponse,
    tags=["PrimaryResource"],
)
def place_proxy_bid(
    auction_id: int = Path(..., description="The ID of the auction to place the bid on"),
    body: BidRequest = ...,
):
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.connect() as conn:
        # Fetch auction details
        result = conn.execute(
            text("SELECT id, title, current_bid, bid_increment, status FROM auctions WHERE id = :aid"),
            {"aid": auction_id},
        )
        auction = result.fetchone()
        
        if auction is None:
            raise HTTPException(status_code=404, detail="Auction not found")
        
        aid, title, current_bid, bid_increment, status = auction
        
        # Check if auction is active
        if status != "active":
            raise HTTPException(status_code=400, detail="Auction is not active")
        
        # Validate bid amount against current bid and increment
        min_required_bid = current_bid + bid_increment
        if body.amount < min_required_bid:
            raise HTTPException(
                status_code=400,
                detail=f"Bid must be at least {min_required_bid} (current bid: {current_bid}, increment: {bid_increment})",
            )
        
        # Validate max_auto_bid >= amount
        if body.max_auto_bid < body.amount:
            raise HTTPException(
                status_code=400,
                detail="max_auto_bid must be greater than or equal to the bid amount",
            )
        
        # Insert the new proxy bid
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_result = conn.execute(
            text("""
                INSERT INTO bids (auction_id, user_id, amount, is_proxy, max_auto_bid, created_at, updated_at)
                VALUES (:auction_id, :user_id, :amount, 1, :max_auto_bid, :created_at, :updated_at)
            """),
            {
                "auction_id": auction_id,
                "user_id": current_user_id,
                "amount": body.amount,
                "max_auto_bid": body.max_auto_bid,
                "created_at": now,
                "updated_at": now,
            },
        )
        
        new_bid_id = insert_result.lastrowid
        
        # Update the auction's current_bid to the new bid amount
        conn.execute(
            text("UPDATE auctions SET current_bid = :new_bid, updated_at = :now WHERE id = :aid"),
            {"new_bid": body.amount, "now": now, "aid": auction_id},
        )
        
        conn.commit()
        
        # Fetch the inserted bid to return it
        bid_result = conn.execute(
            text("""
                SELECT id, auction_id, user_id, amount, is_proxy, max_auto_bid, created_at
                FROM bids WHERE id = :bid_id
            """),
            {"bid_id": new_bid_id},
        )
        bid_row = bid_result.fetchone()
        
        if bid_row is None:
            raise HTTPException(status_code=500, detail="Failed to retrieve inserted bid")
        
        return BidResponse(
            id=bid_row[0],
            auction_id=bid_row[1],
            user_id=bid_row[2],
            amount=float(bid_row[3]),
            is_proxy=int(bid_row[4]),
            max_auto_bid=float(bid_row[5]) if bid_row[5] is not None else 0.0,
            created_at=str(bid_row[6]),
        )
