import os
from datetime import datetime, timedelta
from typing import List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, unique=True)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Auction(Base):
    __tablename__ = "auctions"
    
    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    starting_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="open")
    start_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("starting_price >= 0"),
        CheckConstraint("current_price >= 0"),
        CheckConstraint("status IN ('open', 'closed', 'cancelled')"),
        CheckConstraint("end_time > start_time"),
    )

class Bid(Base):
    __tablename__ = "bids"
    
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    bidder_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("amount > 0"),
    )

# Pydantic models
class AuctionResponse(BaseModel):
    id: int
    title: str
    description: str
    starting_price: float
    current_price: float
    status: str
    start_time: datetime
    end_time: datetime
    
    class Config:
        orm_mode = True

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/auctions/open", response_model=List[AuctionResponse], operation_id="find_open_auctions_closing_soon")
def find_open_auctions_closing_soon():
    """
    Find open auctions closing within the next 7 days, sorted by end time
    """
    db = SessionLocal()
    try:
        # Calculate the date 7 days from now
        seven_days_later = datetime.utcnow() + timedelta(days=7)
        
        # Query for open auctions ending within the next 7 days
        auctions = db.query(Auction).filter(
            Auction.status == "open",
            Auction.end_time <= seven_days_later,
            Auction.end_time > datetime.utcnow()
        ).order_by(Auction.end_time.asc()).all()
        
        return auctions
    finally:
        db.close()
