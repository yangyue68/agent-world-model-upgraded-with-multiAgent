import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, text
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
    full_name = Column(String)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    
    id = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    category = Column(String, nullable=False)
    location_city = Column(String, nullable=False)
    location_state = Column(String, nullable=False)
    description = Column(String)
    starting_bid = Column(Float, nullable=False)
    current_bid = Column(Float)
    current_bidder_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Bid(Base):
    __tablename__ = "bids"
    
    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(String, ForeignKey("lots.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class BidResponse(BaseModel):
    id: int
    lot_id: str
    user_id: int
    amount: float
    created_at: datetime
    updated_at: datetime

class BidUpdateRequest(BaseModel):
    amount: float

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/lots/{lot_id}/bids", response_model=List[BidResponse], operation_id="get_lot_bids")
def get_lot_bids(lot_id: str = Path(..., description="Unique identifier for the lot")):
    db = next(get_db())
    bids = db.query(Bid).filter(Bid.lot_id == lot_id).order_by(Bid.amount.desc(), Bid.created_at.desc()).all()
    return bids

@app.put("/lots/{lot_id}/bids/{bid_id}", response_model=BidResponse, operation_id="update_bid")
def update_bid(
    bid_id: int,
    lot_id: str = Path(..., description="Unique identifier for the lot"),
    bid_update: BidUpdateRequest = None
):
    db = next(get_db())
    
    # Get the current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Fetch the bid to update
    bid = db.query(Bid).filter(Bid.id == bid_id, Bid.lot_id == lot_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="Bid not found")
    
    # Verify the bid belongs to the current user
    if bid.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this bid")
    
    # Update the bid amount
    bid.amount = bid_update.amount
    bid.updated_at = datetime.utcnow()
    
    # Update the lot's current bid if necessary
    lot = db.query(Lot).filter(Lot.id == lot_id).first()
    if lot:
        # Get all bids for this lot ordered by amount descending
        all_bids = db.query(Bid).filter(Bid.lot_id == lot_id).order_by(Bid.amount.desc()).all()
        
        # If this bid is now the highest, update the lot
        if all_bids and all_bids[0].id == bid_id:
            lot.current_bid = bid.amount
            lot.current_bidder_id = bid.user_id
            lot.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(bid)
    return bid
