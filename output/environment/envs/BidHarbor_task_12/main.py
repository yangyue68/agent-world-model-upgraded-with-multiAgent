import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class LotInput(BaseModel):
    lot_number: int
    title: str
    description: Optional[str] = None
    provenance: str
    condition_grade: str
    starting_bid: float


class AuctionCreateRequest(BaseModel):
    seller_id: int
    title: str
    description: Optional[str] = None
    start_time: str
    reserve_price: float
    bid_increment: float
    lots: List[LotInput]


class LotOutput(BaseModel):
    lot_id: int
    lot_number: int


class AuctionCreateResponse(BaseModel):
    auction_id: int
    lots_created: List[LotOutput]


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both T...Z and space-separated forms."""
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Try replacing 'T' with space for some formats
        try:
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid datetime format")


@app.post("/auctions", response_model=AuctionCreateResponse, operation_id="create_live_auction")
def create_live_auction(request: AuctionCreateRequest):
    # Validate seller exists
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id FROM users WHERE id = :seller_id"), {"seller_id": request.seller_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Seller not found")

    # Parse start time
    start_dt = parse_datetime(request.start_time)
    start_time_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Get current user ID from environment
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if current_user_id is None:
        raise HTTPException(status_code=500, detail="Current user ID not available")

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        # Insert auction
        result = conn.execute(
            text("""
                INSERT INTO auctions (seller_id, title, description, auction_type, start_time, end_time, 
                                      reserve_price, bid_increment, status, created_at, updated_at)
                VALUES (:seller_id, :title, :description, 'live', :start_time, NULL, 
                        :reserve_price, :bid_increment, 'scheduled', :created_at, :updated_at)
            """),
            {
                "seller_id": request.seller_id,
                "title": request.title,
                "description": request.description,
                "start_time": start_time_str,
                "reserve_price": request.reserve_price,
                "bid_increment": request.bid_increment,
                "created_at": now_str,
                "updated_at": now_str,
            }
        )
        auction_id = result.lastrowid

        # Insert lots
        lots_created = []
        for lot in request.lots:
            lot_result = conn.execute(
                text("""
                    INSERT INTO lots (auction_id, lot_number, title, description, provenance, condition_grade,
                                      starting_bid, reserve_price, current_bid, bid_increment, status, created_at, updated_at)
                    VALUES (:auction_id, :lot_number, :title, :description, :provenance, :condition_grade,
                            :starting_bid, NULL, NULL, :bid_increment, 'pending', :created_at, :updated_at)
                """),
                {
                    "auction_id": auction_id,
                    "lot_number": lot.lot_number,
                    "title": lot.title,
                    "description": lot.description,
                    "provenance": lot.provenance,
                    "condition_grade": lot.condition_grade,
                    "starting_bid": lot.starting_bid,
                    "bid_increment": request.bid_increment,
                    "created_at": now_str,
                    "updated_at": now_str,
                }
            )
            lot_id = lot_result.lastrowid
            lots_created.append(LotOutput(lot_id=lot_id, lot_number=lot.lot_number))

    return AuctionCreateResponse(auction_id=auction_id, lots_created=lots_created)
