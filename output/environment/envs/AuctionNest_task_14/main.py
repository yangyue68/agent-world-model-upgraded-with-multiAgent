import os
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    display_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Auction(Base):
    __tablename__ = "auctions"
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(String)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True)
    auction_id = Column(String, ForeignKey("auctions.id"), nullable=False)
    lot_number = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    starting_bid = Column(Float, nullable=False)
    bid_increment = Column(Float, nullable=False)
    current_bid = Column(Float)
    current_bidder_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, nullable=False, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ProxyBid(Base):
    __tablename__ = "proxy_bids"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    max_amount = Column(Float, nullable=False)
    bid_increment = Column(Float, nullable=False)
    auto_bid_enabled = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    is_proxy = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class ProxyBidRequest(BaseModel):
    max_amount: float
    bid_increment: float

class ProxyBidResponse(BaseModel):
    id: int
    user_id: int
    lot_id: int
    max_amount: float
    bid_increment: float
    auto_bid_enabled: int
    status: str
    created_at: str
    updated_at: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/auctions/{auction_id}/lots/{lot_id}/proxy_bids", response_model=ProxyBidResponse, operation_id="set_proxy_bid")
def set_proxy_bid(auction_id: str, lot_id: int, proxy_bid: ProxyBidRequest):
    db = next(get_db())
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Verify auction exists and is active
    auction = db.query(Auction).filter(Auction.id == auction_id).first()
    if not auction or auction.status != "active":
        raise HTTPException(status_code=404, detail="Auction not found or not active")
    
    # Verify lot exists and is open
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.auction_id == auction_id).first()
    if not lot or lot.status != "open":
        raise HTTPException(status_code=404, detail="Lot not found or not open")
    
    # Validate bid increment matches lot's bid increment
    if proxy_bid.bid_increment != lot.bid_increment:
        raise HTTPException(status_code=400, detail="Bid increment must match lot's bid increment")
    
    # Check if proxy bid already exists for this user and lot
    existing_proxy_bid = db.query(ProxyBid).filter(
        ProxyBid.user_id == current_user_id,
        ProxyBid.lot_id == lot_id
    ).first()
    
    if existing_proxy_bid:
        # Update existing proxy bid
        existing_proxy_bid.max_amount = proxy_bid.max_amount
        existing_proxy_bid.bid_increment = proxy_bid.bid_increment
        existing_proxy_bid.auto_bid_enabled = 1
        existing_proxy_bid.status = "active"
        existing_proxy_bid.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing_proxy_bid)
        return existing_proxy_bid
    else:
        # Create new proxy bid
        new_proxy_bid = ProxyBid(
            user_id=current_user_id,
            lot_id=lot_id,
            max_amount=proxy_bid.max_amount,
            bid_increment=proxy_bid.bid_increment,
            auto_bid_enabled=1,
            status="active"
        )
        db.add(new_proxy_bid)
        db.commit()
        db.refresh(new_proxy_bid)
        return new_proxy_bid
