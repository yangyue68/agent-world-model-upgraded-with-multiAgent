import os
from datetime import datetime
from typing import List, Literal
from fastapi import FastAPI, HTTPException, Path, Body, Depends
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
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Auction(Base):
    __tablename__ = "auctions"
    id = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(String, ForeignKey("auctions.id"), nullable=False)
    lot_number = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    starting_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    bid_type = Column(String, nullable=False, default="manual")
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    notification_type = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    bid_id = Column(Integer, ForeignKey("bids.id"))
    notification_type = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    message = Column(String)
    sent_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class BidRequest(BaseModel):
    amount: float

class NotificationResponse(BaseModel):
    id: int
    channel: str

class PlaceBidResponse(BaseModel):
    bid_id: int
    notifications_created: List[NotificationResponse]

class LotResponse(BaseModel):
    id: int

class NotificationPreferenceResponse(BaseModel):
    channel: str
    enabled: bool

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/auctions/{auction_id}/lots/{lot_id}/bids", response_model=PlaceBidResponse, operation_id="place_manual_bid")
def place_manual_bid(
    auction_id: str = Path(...),
    lot_id: int = Path(...),
    bid_request: BidRequest = Body(...),
    db = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Validate auction exists and is active
    auction = db.query(Auction).filter(Auction.id == auction_id).first()
    if not auction or auction.status != "active":
        raise HTTPException(status_code=404, detail="Auction not found or not active")
    
    # Validate lot exists and is open
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.auction_id == auction_id).first()
    if not lot or lot.status != "open":
        raise HTTPException(status_code=404, detail="Lot not found or not open")
    
    # Validate bid amount is greater than current price
    if bid_request.amount <= lot.current_price:
        raise HTTPException(status_code=400, detail="Bid amount must be greater than current price")
    
    # Create bid
    new_bid = Bid(
        lot_id=lot_id,
        user_id=current_user_id,
        amount=bid_request.amount,
        bid_type="manual",
        status="active"
    )
    db.add(new_bid)
    db.flush()  # Get the bid ID without committing
    
    # Update lot's current price
    lot.current_price = bid_request.amount
    db.commit()
    db.refresh(new_bid)
    
    # Check notification preferences for outbid alerts
    notification_prefs = db.query(NotificationPreference).filter(
        NotificationPreference.user_id == current_user_id,
        NotificationPreference.notification_type == "outbid",
        NotificationPreference.enabled == 1
    ).all()
    
    notifications_created = []
    
    # Create notifications for enabled channels
    for pref in notification_prefs:
        if pref.channel in ["email", "sms"]:
            new_notification = Notification(
                user_id=current_user_id,
                bid_id=new_bid.id,
                notification_type="outbid",
                channel=pref.channel,
                status="pending"
            )
            db.add(new_notification)
            db.flush()
            notifications_created.append(NotificationResponse(id=new_notification.id, channel=pref.channel))
    
    db.commit()
    
    return PlaceBidResponse(
        bid_id=new_bid.id,
        notifications_created=notifications_created
    )

@app.get("/auctions/{auction_id}/lots", response_model=LotResponse, operation_id="find_lot_by_auction_and_number")
def find_lot_by_auction_and_number(
    auction_id: str = Path(...),
    lot_number: int = Path(...),
    db = Depends(get_db)
):
    lot = db.query(Lot).filter(
        Lot.auction_id == auction_id,
        Lot.lot_number == lot_number
    ).first()
    
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    
    return LotResponse(id=lot.id)

@app.get("/auctions/{auction_id}/lots/{lot_id}/notification_preferences", response_model=List[NotificationPreferenceResponse], operation_id="get_lot_notification_preferences")
def get_lot_notification_preferences(
    auction_id: str = Path(...),
    lot_id: int = Path(...),
    db = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the lot belongs to the auction
    lot = db.query(Lot).filter(
        Lot.id == lot_id,
        Lot.auction_id == auction_id
    ).first()
    
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    
    # Get notification preferences for the current user
    prefs = db.query(NotificationPreference).filter(
        NotificationPreference.user_id == current_user_id,
        NotificationPreference.notification_type == "outbid"
    ).all()
    
    return [
        NotificationPreferenceResponse(channel=pref.channel, enabled=bool(pref.enabled))
        for pref in prefs
    ]
