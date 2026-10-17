from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True, index=True)
    state = Column(String, nullable=False)
    city = Column(String)
    zip_code = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class AuctionLot(Base):
    __tablename__ = "auction_lots"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    condition = Column(String, nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    starting_bid = Column(Float, nullable=False)
    current_bid = Column(Float)
    auction_end_time = Column(DateTime, nullable=False)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("condition IN ('New', 'Used - Like New', 'Used - Good', 'Used - Fair', 'For Parts')"),
        CheckConstraint("status IN ('active', 'ended', 'cancelled')"),
        CheckConstraint("starting_bid >= 0"),
        CheckConstraint("current_bid >= 0"),
    )

app = FastAPI()

class AuctionLotResponse(BaseModel):
    id: int
    title: str
    description: str
    category_id: int
    condition: str
    location_id: int
    seller_id: int
    starting_bid: float
    current_bid: float
    auction_end_time: str
    status: str

@app.get("/auction-lots/active", response_model=list[AuctionLotResponse], operation_id="find_active_auction_lots")
def find_active_auction_lots(
    category_name: str = Query(..., description="Category name to filter by"),
    condition: str = Query(..., description="Condition to filter by"),
    state: str = Query(..., description="State to filter by"),
    limit: int = Query(5, description="Maximum number of results to return")
):
    db = SessionLocal()
    try:
        lots = db.query(AuctionLot)\
            .join(Category, AuctionLot.category_id == Category.id)\
            .join(Location, AuctionLot.location_id == Location.id)\
            .filter(
                Category.name == category_name,
                AuctionLot.condition == condition,
                Location.state == state,
                AuctionLot.status == "active"
            )\
            .order_by(AuctionLot.auction_end_time.asc())\
            .limit(limit)\
            .all()
        
        return [
            AuctionLotResponse(
                id=lot.id,
                title=lot.title,
                description=lot.description,
                category_id=lot.category_id,
                condition=lot.condition,
                location_id=lot.location_id,
                seller_id=lot.seller_id,
                starting_bid=lot.starting_bid,
                current_bid=lot.current_bid if lot.current_bid is not None else 0.0,
                auction_end_time=lot.auction_end_time.isoformat() if isinstance(lot.auction_end_time, datetime) else lot.auction_end_time,
                status=lot.status
            ) for lot in lots
        ]
    finally:
        db.close()
