import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, CheckConstraint, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Auction(Base):
    __tablename__ = "auctions"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    status = Column(String, nullable=False, default="draft")
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    lot_number = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    starting_bid = Column(Float, nullable=False)
    reserve_price = Column(Float)
    bid_increment = Column(Float, nullable=False, default=1.0)
    condition_report = Column(Text)
    provenance = Column(Text)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class LiveAuctionEvent(Base):
    __tablename__ = "live_auction_events"
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    event_name = Column(String, nullable=False)
    event_type = Column(String, nullable=False, default="virtual")
    scheduled_start = Column(DateTime, nullable=False)
    scheduled_end = Column(DateTime)
    timezone = Column(String, nullable=False, default="UTC")
    stream_url = Column(String)
    status = Column(String, nullable=False, default="scheduled")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Request Models
class CreateLotRequest(BaseModel):
    title: str
    description: Optional[str] = None
    starting_bid: float
    reserve_price: Optional[float] = None
    bid_increment: float
    condition_report: Optional[str] = None
    provenance: Optional[str] = None

class ScheduleLiveEventRequest(BaseModel):
    event_name: str
    event_type: Optional[str] = "virtual"
    scheduled_start: str
    scheduled_end: Optional[str] = None
    timezone: str
    stream_url: Optional[str] = None

# Response Models
class LotResponse(BaseModel):
    id: int
    auction_id: int
    lot_number: int
    title: str
    description: Optional[str] = None
    starting_bid: float
    reserve_price: Optional[float] = None
    bid_increment: float
    condition_report: Optional[str] = None
    provenance: Optional[str] = None
    status: str
    created_at: str
    updated_at: str

class LiveEventResponse(BaseModel):
    id: int
    auction_id: int
    event_name: str
    event_type: str
    scheduled_start: str
    scheduled_end: Optional[str] = None
    timezone: str
    stream_url: Optional[str] = None
    status: str
    created_by: int
    created_at: str
    updated_at: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/auctions/{auction_id}/lots", response_model=LotResponse, operation_id="create_lot_for_auction")
def create_lot_for_auction(auction_id: int, lot_data: CreateLotRequest):
    db = next(get_db())
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Check if auction exists
    auction = db.query(Auction).filter(Auction.id == auction_id).first()
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")
    
    # Get max lot number for this auction
    max_lot_number = db.query(func.max(Lot.lot_number)).filter(Lot.auction_id == auction_id).scalar() or 0
    
    # Create new lot
    new_lot = Lot(
        auction_id=auction_id,
        lot_number=max_lot_number + 1,
        title=lot_data.title,
        description=lot_data.description,
        starting_bid=lot_data.starting_bid,
        reserve_price=lot_data.reserve_price,
        bid_increment=lot_data.bid_increment,
        condition_report=lot_data.condition_report,
        provenance=lot_data.provenance,
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(new_lot)
    db.commit()
    db.refresh(new_lot)
    
    return new_lot

@app.post("/auctions/{auction_id}/live_events", response_model=LiveEventResponse, operation_id="schedule_live_auction_event")
def schedule_live_auction_event(auction_id: int, event_data: ScheduleLiveEventRequest):
    db = next(get_db())
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Check if auction exists
    auction = db.query(Auction).filter(Auction.id == auction_id).first()
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")
    
    # Parse datetime strings
    scheduled_start = datetime.fromisoformat(event_data.scheduled_start.replace('Z', '+00:00'))
    scheduled_end = None
    if event_data.scheduled_end:
        scheduled_end = datetime.fromisoformat(event_data.scheduled_end.replace('Z', '+00:00'))
    
    # Create new live event
    new_event = LiveAuctionEvent(
        auction_id=auction_id,
        event_name=event_data.event_name,
        event_type=event_data.event_type,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        timezone=event_data.timezone,
        stream_url=event_data.stream_url,
        status="scheduled",
        created_by=current_user_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    
    return new_event
