import os
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, func, case
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
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    starting_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    bid_increment = Column(Float, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    original_end_time = Column(DateTime, nullable=False)
    anti_sniping_window_seconds = Column(Integer, nullable=False, default=300)
    anti_sniping_extension_seconds = Column(Integer, nullable=False, default=300)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    bidder_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    is_proxy = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class ProxyBid(Base):
    __tablename__ = "proxy_bids"
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    max_amount = Column(Float, nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class AuctionExtension(Base):
    __tablename__ = "auction_extensions"
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    triggering_bid_id = Column(Integer, ForeignKey("bids.id"), nullable=False)
    previous_end_time = Column(DateTime, nullable=False)
    new_end_time = Column(DateTime, nullable=False)
    extension_seconds = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class AuctionResponse(BaseModel):
    id: int
    title: str
    end_time: datetime
    current_price: float
    bid_increment: float

class BidResponse(BaseModel):
    id: int
    amount: float
    created_at: datetime

class ExtensionResponse(BaseModel):
    id: int
    new_end_time: datetime
    created_at: datetime

class ProxyBidResponse(BaseModel):
    auction_id: int
    max_amount: float

class IncreaseProxyBidResponse(BaseModel):
    success: bool
    new_amount: float

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/auctions/ending_soon", response_model=List[AuctionResponse], operation_id="find_ending_soon_auctions")
def find_ending_soon_auctions(db = Depends(get_db)):
    now = datetime.utcnow()
    one_hour_later = now + timedelta(hours=1)
    
    auctions = db.query(Auction).filter(
        Auction.status == "active",
        Auction.end_time >= now,
        Auction.end_time <= one_hour_later
    ).all()
    
    return [
        AuctionResponse(
            id=auction.id,
            title=auction.title,
            end_time=auction.end_time,
            current_price=auction.current_price,
            bid_increment=auction.bid_increment
        ) for auction in auctions
    ]

@app.get("/auctions/{auction_id}/recent_bids", response_model=List[BidResponse], operation_id="get_recent_bids_for_auction")
def get_recent_bids_for_auction(auction_id: int, db = Depends(get_db)):
    now = datetime.utcnow()
    five_minutes_ago = now - timedelta(minutes=5)
    
    bids = db.query(Bid).filter(
        Bid.auction_id == auction_id,
        Bid.created_at >= five_minutes_ago
    ).all()
    
    return [
        BidResponse(
            id=bid.id,
            amount=bid.amount,
            created_at=bid.created_at
        ) for bid in bids
    ]

@app.get("/auctions/{auction_id}/extensions", response_model=List[ExtensionResponse], operation_id="get_auction_extensions")
def get_auction_extensions(auction_id: int, db = Depends(get_db)):
    extensions = db.query(AuctionExtension).filter(
        AuctionExtension.auction_id == auction_id
    ).all()
    
    return [
        ExtensionResponse(
            id=extension.id,
            new_end_time=extension.new_end_time,
            created_at=extension.created_at
        ) for extension in extensions
    ]

@app.get("/proxy_bids/current", response_model=List[ProxyBidResponse], operation_id="find_current_proxy_bids")
def find_current_proxy_bids(db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    proxy_bids = db.query(ProxyBid).filter(
        ProxyBid.user_id == current_user_id,
        ProxyBid.is_active == 1
    ).all()
    
    return [
        ProxyBidResponse(
            auction_id=proxy_bid.auction_id,
            max_amount=proxy_bid.max_amount
        ) for proxy_bid in proxy_bids
    ]

@app.post("/proxy_bids/increase", response_model=IncreaseProxyBidResponse, operation_id="increase_proxy_bid")
def increase_proxy_bid(db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Find active proxy bids for the current user
    proxy_bids = db.query(ProxyBid).filter(
        ProxyBid.user_id == current_user_id,
        ProxyBid.is_active == 1
    ).all()
    
    # Get auctions ending soon
    now = datetime.utcnow()
    one_hour_later = now + timedelta(hours=1)
    ending_soon_auctions = db.query(Auction).filter(
        Auction.status == "active",
        Auction.end_time >= now,
        Auction.end_time <= one_hour_later
    ).all()
    
    # For each auction, check if it was recently extended
    for auction in ending_soon_auctions:
        # Check if there were recent bids in the last 5 minutes
        five_minutes_ago = now - timedelta(minutes=5)
        recent_bids = db.query(Bid).filter(
            Bid.auction_id == auction.id,
            Bid.created_at >= five_minutes_ago
        ).all()
        
        # If there are recent bids, check if any triggered an extension
        if recent_bids:
            # Check for extensions in the last 5 minutes
            recent_extensions = db.query(AuctionExtension).filter(
                AuctionExtension.auction_id == auction.id,
                AuctionExtension.created_at >= five_minutes_ago
            ).all()
            
            # If there were extensions, we need to increase proxy bid
            if recent_extensions:
                # Find the proxy bid for this auction
                proxy_bid = db.query(ProxyBid).filter(
                    ProxyBid.auction_id == auction.id,
                    ProxyBid.user_id == current_user_id
                ).first()
                
                if proxy_bid:
                    # Increase by one bid increment
                    new_amount = proxy_bid.max_amount + auction.bid_increment
                    
                    # Update the proxy bid
                    proxy_bid.max_amount = new_amount
                    proxy_bid.updated_at = datetime.utcnow()
                    db.commit()
                    
                    return IncreaseProxyBidResponse(
                        success=True,
                        new_amount=new_amount
                    )
    
    # If no conditions met, return failure
    return IncreaseProxyBidResponse(
        success=False,
        new_amount=0.0
    )
