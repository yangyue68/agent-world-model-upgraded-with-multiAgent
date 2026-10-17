import os
from datetime import datetime
from typing import List, Literal
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Auction(Base):
    __tablename__ = "auctions"
    id = Column(Integer, primary_key=True)
    auction_code = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime)
    status = Column(String, nullable=False, default="scheduled")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    lot_number = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    starting_price = Column(Float, nullable=False)
    current_price = Column(Float)
    status = Column(String, nullable=False, default="upcoming")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Watchlist(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class LiveSession(Base):
    __tablename__ = "live_sessions"
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow)
    left_at = Column(DateTime)

# Pydantic Models
class JoinLiveAuctionResponse(BaseModel):
    session_id: int
    auction_id: int
    user_id: int
    joined_at: datetime

class PlaceBidRequest(BaseModel):
    amount: float

class PlaceBidResponse(BaseModel):
    bid_id: int
    lot_id: int
    user_id: int
    amount: float
    created_at: datetime

class AddToWatchlistRequest(BaseModel):
    lot_ids: List[int]

class AddToWatchlistResponseItem(BaseModel):
    lot_id: int
    user_id: int
    created_at: datetime

class AddToWatchlistResponse(BaseModel):
    added_items: List[AddToWatchlistResponseItem]

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/auctions/{auction_id}/join", response_model=JoinLiveAuctionResponse, operation_id="join_live_auction")
def join_live_auction(auction_id: int, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Check if auction exists and is live
    auction = db.query(Auction).filter(Auction.id == auction_id).first()
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")
    
    if auction.status != "live":
        raise HTTPException(status_code=400, detail="Auction is not live")
    
    # Create live session
    live_session = LiveSession(
        auction_id=auction_id,
        user_id=current_user_id
    )
    db.add(live_session)
    db.commit()
    db.refresh(live_session)
    
    return {
        "session_id": live_session.id,
        "auction_id": live_session.auction_id,
        "user_id": live_session.user_id,
        "joined_at": live_session.joined_at
    }

@app.post("/lots/{lot_id}/bid", response_model=PlaceBidResponse, operation_id="place_bid")
def place_bid(lot_id: int, bid_request: PlaceBidRequest, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Check if lot exists and is active
    lot = db.query(Lot).filter(Lot.id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    
    if lot.status != "active":
        raise HTTPException(status_code=400, detail="Lot is not active")
    
    # Check if bid amount is higher than current price
    current_bid_amount = 0
    if lot.current_price:
        current_bid_amount = lot.current_price
    
    if bid_request.amount <= current_bid_amount:
        raise HTTPException(status_code=400, detail="Bid amount must be higher than current bid")
    
    # Create bid
    bid = Bid(
        lot_id=lot_id,
        user_id=current_user_id,
        amount=bid_request.amount
    )
    db.add(bid)
    db.commit()
    db.refresh(bid)
    
    # Update lot's current price
    lot.current_price = bid_request.amount
    db.commit()
    
    return {
        "bid_id": bid.id,
        "lot_id": bid.lot_id,
        "user_id": bid.user_id,
        "amount": bid.amount,
        "created_at": bid.created_at
    }

@app.post("/watchlist", response_model=AddToWatchlistResponse, operation_id="add_to_watchlist")
def add_to_watchlist(watchlist_request: AddToWatchlistRequest, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    added_items = []
    
    for lot_id in watchlist_request.lot_ids:
        # Check if lot exists
        lot = db.query(Lot).filter(Lot.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Lot {lot_id} not found")
        
        # Check if already in watchlist
        existing = db.query(Watchlist).filter(
            Watchlist.user_id == current_user_id,
            Watchlist.lot_id == lot_id
        ).first()
        
        if not existing:
            # Add to watchlist
            watchlist_item = Watchlist(
                user_id=current_user_id,
                lot_id=lot_id
            )
            db.add(watchlist_item)
            db.commit()
            db.refresh(watchlist_item)
            
            added_items.append({
                "lot_id": watchlist_item.lot_id,
                "user_id": watchlist_item.user_id,
                "created_at": watchlist_item.created_at
            })
    
    return {"added_items": added_items}
