import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
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
    title = Column(String, nullable=False)
    description = Column(String)
    status = Column(String, nullable=False, default="live")
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    lot_number = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    starting_price = Column(Float, nullable=False, default=0.0)
    current_price = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    is_auto = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class AutoBidConfig(Base):
    __tablename__ = "auto_bid_configs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    max_amount = Column(Float, nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    message = Column(String)
    related_lot_id = Column(Integer, ForeignKey("lots.id"))
    related_bid_id = Column(Integer, ForeignKey("bids.id"))
    is_read = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    notify_on_outbid = Column(Integer, nullable=False, default=1)
    notify_on_auction_won = Column(Integer, nullable=False, default=1)
    notify_on_auction_lost = Column(Integer, nullable=False, default=1)
    notify_on_auto_bid_placed = Column(Integer, nullable=False, default=1)
    notify_on_auto_bid_exceeded = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class AutoBidConfigResponse(BaseModel):
    id: int
    user_id: int
    lot_id: int
    max_amount: float
    is_active: int

class NotificationResponse(BaseModel):
    id: int
    user_id: int
    type: str
    title: str
    message: str
    related_lot_id: Optional[int]
    related_bid_id: Optional[int]
    is_read: int

class NotificationReadResponse(BaseModel):
    id: int
    is_read: int

class AutoBidRequest(BaseModel):
    max_amount: float

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/auctions/{auction_id}/lots/{lot_id}/auto-bid", response_model=AutoBidConfigResponse, operation_id="configure_auto_bid")
def configure_auto_bid(auction_id: int, lot_id: int, auto_bid_request: AutoBidRequest, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the lot belongs to the auction
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.auction_id == auction_id).first()
    if not lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lot not found in auction")
    
    # Create new auto-bid config
    auto_bid_config = AutoBidConfig(
        user_id=current_user_id,
        lot_id=lot_id,
        max_amount=auto_bid_request.max_amount,
        is_active=1
    )
    db.add(auto_bid_config)
    db.commit()
    db.refresh(auto_bid_config)
    
    return auto_bid_config

@app.get("/auctions/{auction_id}/lots/{lot_id}/auto-bid", response_model=AutoBidConfigResponse, operation_id="get_auto_bid_config")
def get_auto_bid_config(auction_id: int, lot_id: int, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the lot belongs to the auction
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.auction_id == auction_id).first()
    if not lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lot not found in auction")
    
    # Get auto-bid config
    auto_bid_config = db.query(AutoBidConfig).filter(
        AutoBidConfig.user_id == current_user_id,
        AutoBidConfig.lot_id == lot_id
    ).first()
    
    if not auto_bid_config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto-bid configuration not found")
    
    return auto_bid_config

@app.put("/auctions/{auction_id}/lots/{lot_id}/auto-bid", response_model=AutoBidConfigResponse, operation_id="update_auto_bid_config")
def update_auto_bid_config(auction_id: int, lot_id: int, auto_bid_request: AutoBidRequest, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the lot belongs to the auction
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.auction_id == auction_id).first()
    if not lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lot not found in auction")
    
    # Get existing auto-bid config
    auto_bid_config = db.query(AutoBidConfig).filter(
        AutoBidConfig.user_id == current_user_id,
        AutoBidConfig.lot_id == lot_id
    ).first()
    
    if not auto_bid_config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto-bid configuration not found")
    
    # Update auto-bid config
    auto_bid_config.max_amount = auto_bid_request.max_amount
    auto_bid_config.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(auto_bid_config)
    
    return auto_bid_config

@app.delete("/auctions/{auction_id}/lots/{lot_id}/auto-bid", response_model=dict, operation_id="remove_auto_bid_config")
def remove_auto_bid_config(auction_id: int, lot_id: int, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the lot belongs to the auction
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.auction_id == auction_id).first()
    if not lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lot not found in auction")
    
    # Get existing auto-bid config
    auto_bid_config = db.query(AutoBidConfig).filter(
        AutoBidConfig.user_id == current_user_id,
        AutoBidConfig.lot_id == lot_id
    ).first()
    
    if not auto_bid_config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto-bid configuration not found")
    
    # Delete auto-bid config
    db.delete(auto_bid_config)
    db.commit()
    
    return {"message": "Auto-bid configuration removed"}

@app.get("/notifications", response_model=List[NotificationResponse], operation_id="get_notifications")
def get_notifications(db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    notifications = db.query(Notification).filter(
        Notification.user_id == current_user_id
    ).order_by(Notification.created_at.desc()).all()
    
    return notifications

@app.put("/notifications/{notification_id}/read", response_model=NotificationReadResponse, operation_id="mark_notification_as_read")
def mark_notification_as_read(notification_id: int, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user_id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    
    notification.is_read = 1
    notification.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(notification)
    
    return notification
