import os
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, status, Depends
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, ForeignKey, text, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
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
    name = Column(String, nullable=False)
    description = Column(String)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    bid_increment = Column(Float, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
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
    reserve_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Request and Response Models
class DuplicateAuctionRequest(BaseModel):
    source_auction_id: int
    new_name: str
    start_time: str
    end_time: str

class AuctionResponse(BaseModel):
    id: int
    name: str
    description: str
    start_time: str
    end_time: str
    bid_increment: float
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

@app.post("/auctions/duplicate", response_model=AuctionResponse, operation_id="duplicate_auction")
def duplicate_auction(request: DuplicateAuctionRequest, db: Session = Depends(get_db)):
    # Get the current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Validate source auction exists
    source_auction = db.query(Auction).filter(Auction.id == request.source_auction_id).first()
    if not source_auction:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source auction not found")
    
    # Validate that the current user owns the source auction
    if source_auction.created_by != current_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: You don't own this auction")
    
    # Parse the new start and end times
    try:
        start_time = datetime.fromisoformat(request.start_time.replace(' ', 'T'))
        end_time = datetime.fromisoformat(request.end_time.replace(' ', 'T'))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date format")
    
    # Create new auction with modified parameters
    new_auction = Auction(
        name=request.new_name,
        description=source_auction.description,
        start_time=start_time,
        end_time=end_time,
        bid_increment=500.0,  # Fixed bid increment as specified
        created_by=current_user_id
    )
    
    db.add(new_auction)
    db.flush()  # Get the new auction ID
    
    # Copy lots with 10% higher reserve prices
    lots = db.query(Lot).filter(Lot.auction_id == request.source_auction_id).all()
    
    for lot in lots:
        new_lot = Lot(
            auction_id=new_auction.id,
            lot_number=lot.lot_number,
            title=lot.title,
            description=lot.description,
            starting_price=lot.starting_price,
            reserve_price=lot.reserve_price * 1.1  # 10% higher reserve price
        )
        db.add(new_lot)
    
    db.commit()
    db.refresh(new_auction)
    
    # Return the new auction details
    return AuctionResponse(
        id=new_auction.id,
        name=new_auction.name,
        description=new_auction.description,
        start_time=new_auction.start_time.isoformat(),
        end_time=new_auction.end_time.isoformat(),
        bid_increment=new_auction.bid_increment,
        created_by=new_auction.created_by,
        created_at=new_auction.created_at.isoformat(),
        updated_at=new_auction.updated_at.isoformat()
    )
