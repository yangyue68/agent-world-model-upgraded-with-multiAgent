import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class BidHistoryItem(BaseModel):
    id: int
    bidder_id: int
    amount: float
    bid_type: str
    created_at: str


class PlaceBidRequest(BaseModel):
    amount: float


class BidResponse(BaseModel):
    id: int
    lot_id: int
    bidder_id: int
    amount: float
    bid_type: str
    created_at: str


@app.get("/lots/{lot_id}/bid_history", response_model=List[BidHistoryItem], operation_id="get_lot_bid_history")
def get_lot_bid_history(lot_id: int = Path(..., description="Internal ID of the lot")):
    db = SessionLocal()
    try:
        # Verify lot exists
        lot_query = text("SELECT id FROM lots WHERE id = :lot_id")
        lot_result = db.execute(lot_query, {"lot_id": lot_id}).fetchone()
        if not lot_result:
            raise HTTPException(status_code=404, detail="Lot not found")

        # Get bid history ordered by amount descending, then creation time ascending
        query = text("""
            SELECT id, bidder_id, amount, bid_type, created_at 
            FROM bids 
            WHERE lot_id = :lot_id 
            ORDER BY amount DESC, created_at ASC
        """)
        results = db.execute(query, {"lot_id": lot_id}).fetchall()
        
        return [
            BidHistoryItem(
                id=row[0],
                bidder_id=row[1],
                amount=float(row[2]),
                bid_type=row[3],
                created_at=str(row[4])
            )
            for row in results
        ]
    finally:
        db.close()


@app.post("/lots/{lot_id}/bids", response_model=BidResponse, operation_id="place_bid_on_lot")
def place_bid_on_lot(
    lot_id: int = Path(..., description="Internal ID of the lot"),
    request_body: PlaceBidRequest = ...
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    db = SessionLocal()
    try:
        # Verify lot exists and is active
        lot_query = text("SELECT id, status, bid_increment, current_bid FROM lots WHERE id = :lot_id")
        lot_result = db.execute(lot_query, {"lot_id": lot_id}).fetchone()
        
        if not lot_result:
            raise HTTPException(status_code=404, detail="Lot not found")
        
        _, status, bid_increment, current_bid = lot_result
        
        if status != 'active':
            raise HTTPException(status_code=400, detail="Lot is not active")

        # Determine minimum valid bid
        min_bid = 0.0
        if current_bid is not None:
            min_bid = float(current_bid) + float(bid_increment)
        else:
            # If no current bid, check starting bid? Spec says "greater than current highest bid".
            # Usually starting_bid is the floor if no bids yet. Let's assume starting_bid is the base.
            start_query = text("SELECT starting_bid FROM lots WHERE id = :lot_id")
            start_res = db.execute(start_query, {"lot_id": lot_id}).fetchone()
            if start_res:
                min_bid = float(start_res[0]) + float(bid_increment)
            else:
                 min_bid = float(bid_increment) # Fallback

        if request_body.amount < min_bid:
            raise HTTPException(
                status_code=400, 
                detail=f"Bid amount must be at least {min_bid}"
            )

        # Insert new bid
        insert_query = text("""
            INSERT INTO bids (lot_id, bidder_id, amount, bid_type, created_at)
            VALUES (:lot_id, :bidder_id, :amount, 'straight', CURRENT_TIMESTAMP)
        """)
        result = db.execute(insert_query, {
            "lot_id": lot_id,
            "bidder_id": current_user_id,
            "amount": request_body.amount
        })
        new_bid_id = result.lastrowid

        # Update lot with new current bid and bidder
        update_lot_query = text("""
            UPDATE lots 
            SET current_bid = :amount, 
                current_bidder_id = :bidder_id, 
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :lot_id
        """)
        db.execute(update_lot_query, {
            "amount": request_body.amount,
            "bidder_id": current_user_id,
            "lot_id": lot_id
        })

        db.commit()

        # Fetch the created bid to return details including timestamp
        fetch_query = text("""
            SELECT id, lot_id, bidder_id, amount, bid_type, created_at 
            FROM bids 
            WHERE id = :bid_id
        """)
        bid_row = db.execute(fetch_query, {"bid_id": new_bid_id}).fetchone()

        return BidResponse(
            id=bid_row[0],
            lot_id=bid_row[1],
            bidder_id=bid_row[2],
            amount=float(bid_row[3]),
            bid_type=bid_row[4],
            created_at=str(bid_row[5])
        )

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
