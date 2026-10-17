from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import os
from datetime import datetime

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Auction(Base):
    __tablename__ = "auctions"
    
    id = Column(Integer, primary_key=True)
    seller_id = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    reserve_price = Column(Float, nullable=False)
    current_bid = Column(Float)
    high_bidder_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, nullable=False, default="active")
    start_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("reserve_price >= 0"),
        CheckConstraint("current_bid >= 0"),
        CheckConstraint("status IN ('active', 'ended', 'cancelled')"),
    )

class Bid(Base):
    __tablename__ = "bids"
    
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, nullable=False)
    bidder_id = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("amount > 0"),
    )

class ReminderEmail(Base):
    __tablename__ = "reminder_emails"
    
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, nullable=False)
    recipient_id = Column(Integer, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, nullable=False, default="sent")
    
    __table_args__ = (
        CheckConstraint("status IN ('sent', 'failed')"),
    )

# Pydantic models
class AuctionResponse(BaseModel):
    id: int
    title: str
    reserve_price: float
    current_bid: float
    high_bidder_id: int
    status: str
    end_time: datetime

class AuctionMeetingReserveResponse(BaseModel):
    auction_id: int
    title: str
    reserve_price: float
    current_bid: float

class ReminderEmailRequest(BaseModel):
    auction_id: int
    recipient_id: int

class ReminderEmailResponse(BaseModel):
    id: int
    auction_id: int
    recipient_id: int
    sent_at: datetime
    status: str

# Create app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/auctions/active/seller/{seller_id}", response_model=list[AuctionResponse], operation_id="find_active_auctions_by_seller")
def find_active_auctions_by_seller(seller_id: int, db: Session = Depends(get_db)):
    auctions = db.query(Auction).filter(
        Auction.seller_id == seller_id,
        Auction.status == "active"
    ).all()
    return auctions

@app.get("/auctions/meet_reserve", response_model=list[AuctionMeetingReserveResponse], operation_id="find_auctions_meeting_reserve")
def find_auctions_meeting_reserve(db: Session = Depends(get_db)):
    # Find auctions where current_bid >= reserve_price
    auctions = db.query(
        Auction.id.label("auction_id"),
        Auction.title,
        Auction.reserve_price,
        Auction.current_bid
    ).filter(
        Auction.current_bid >= Auction.reserve_price
    ).all()
    
    return [
        AuctionMeetingReserveResponse(
            auction_id=auction_id,
            title=title,
            reserve_price=reserve_price,
            current_bid=current_bid
        ) for auction_id, title, reserve_price, current_bid in auctions
    ]

@app.post("/reminder_emails", response_model=ReminderEmailResponse, operation_id="send_reminder_email")
def send_reminder_email(request: ReminderEmailRequest, db: Session = Depends(get_db)):
    # Verify that the auction exists and belongs to the current user
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    auction = db.query(Auction).filter(
        Auction.id == request.auction_id,
        Auction.seller_id == current_user_id
    ).first()
    
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found or not owned by current user")
    
    # Verify that the recipient is a valid user
    recipient = db.query(User).filter(User.id == request.recipient_id).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    # Create reminder email record
    reminder = ReminderEmail(
        auction_id=request.auction_id,
        recipient_id=request.recipient_id
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder

# Task implementation
@app.get("/task_4", response_model=list[dict])
def task_4():
    """
    For all active auctions I'm selling, check if any have met their reserve price,
    and for those that haven't, send a reminder email to the high bidder asking if 
    they'd like to increase their bid.
    """
    db = SessionLocal()
    try:
        # Get current user ID from environment
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
        
        # Find all active auctions sold by the current user
        active_auctions = db.query(Auction).filter(
            Auction.seller_id == current_user_id,
            Auction.status == "active"
        ).all()
        
        results = []
        
        # Process each auction
        for auction in active_auctions:
            # Check if auction has met reserve price
            if auction.current_bid >= auction.reserve_price:
                # Auction has met reserve price - no action needed
                results.append({
                    "auction_id": auction.id,
                    "action": "no_action",
                    "reason": "Auction has met reserve price"
                })
            else:
                # Auction hasn't met reserve price - send reminder email
                # Send reminder email to high bidder
                reminder = ReminderEmail(
                    auction_id=auction.id,
                    recipient_id=auction.high_bidder_id
                )
                db.add(reminder)
                db.commit()
                db.refresh(reminder)
                
                results.append({
                    "auction_id": auction.id,
                    "action": "reminder_sent",
                    "recipient_id": reminder.recipient_id,
                    "reminder_id": reminder.id
                })
        
        return results
    
    finally:
        db.close()
