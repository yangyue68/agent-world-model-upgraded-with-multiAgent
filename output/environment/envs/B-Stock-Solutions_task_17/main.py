import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, CheckConstraint
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
    email = Column(String)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Marketplace(Base):
    __tablename__ = "marketplaces"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Condition(Base):
    __tablename__ = "conditions"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class BulkLot(Base):
    __tablename__ = "bulk_lots"
    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    marketplace_id = Column(Integer, ForeignKey("marketplaces.id"), nullable=False)
    condition_id = Column(Integer, ForeignKey("conditions.id"), nullable=False)
    title = Column(String, nullable=False)
    total_retail_value = Column(Float, nullable=False)
    starting_bid = Column(Float, nullable=False)
    reserve_price = Column(Float)
    bid_increment = Column(Float, nullable=False)
    auction_start = Column(DateTime, nullable=False)
    auction_end = Column(DateTime, nullable=False)
    shipping_cost = Column(Float, nullable=False)
    shipping_method = Column(String, nullable=False)
    local_pickup_location = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    seller = relationship("User")
    marketplace = relationship("Marketplace")
    condition = relationship("Condition")

class ManifestItem(Base):
    __tablename__ = "manifest_items"
    id = Column(Integer, primary_key=True, index=True)
    bulk_lot_id = Column(Integer, ForeignKey("bulk_lots.id", ondelete="CASCADE"), nullable=False)
    sku = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_retail_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    bulk_lot = relationship("BulkLot")

# Pydantic Models
class ManifestItemRequest(BaseModel):
    sku: str
    quantity: int
    unit_retail_price: float

class CreateBulkLotRequest(BaseModel):
    seller_id: int
    marketplace_id: int
    condition_id: int
    title: str
    total_retail_value: float
    starting_bid: float
    reserve_price: Optional[float] = None
    bid_increment: float
    auction_start: str
    auction_end: str
    shipping_cost: float
    shipping_method: str
    local_pickup_location: Optional[str] = None
    manifest_items: List[ManifestItemRequest]

class CreateBulkLotResponse(BaseModel):
    bulk_lot_id: int
    message: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/bulk_lots", response_model=CreateBulkLotResponse, operation_id="create_bulk_lot")
def create_bulk_lot(request: CreateBulkLotRequest):
    db = next(get_db())
    
    # Parse datetime strings
    auction_start = datetime.fromisoformat(request.auction_start.replace('Z', '+00:00'))
    auction_end = datetime.fromisoformat(request.auction_end.replace('Z', '+00:00'))
    
    # Validate auction times
    if auction_end <= auction_start:
        raise HTTPException(status_code=400, detail="Auction end time must be after start time")
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if current_user_id != request.seller_id:
        raise HTTPException(status_code=403, detail="Seller ID does not match authenticated user")
    
    # Create bulk lot
    bulk_lot = BulkLot(
        seller_id=request.seller_id,
        marketplace_id=request.marketplace_id,
        condition_id=request.condition_id,
        title=request.title,
        total_retail_value=request.total_retail_value,
        starting_bid=request.starting_bid,
        reserve_price=request.reserve_price,
        bid_increment=request.bid_increment,
        auction_start=auction_start,
        auction_end=auction_end,
        shipping_cost=request.shipping_cost,
        shipping_method=request.shipping_method,
        local_pickup_location=request.local_pickup_location
    )
    
    db.add(bulk_lot)
    db.commit()
    db.refresh(bulk_lot)
    
    # Create manifest items
    for item_data in request.manifest_items:
        manifest_item = ManifestItem(
            bulk_lot_id=bulk_lot.id,
            sku=item_data.sku,
            quantity=item_data.quantity,
            unit_retail_price=item_data.unit_retail_price
        )
        db.add(manifest_item)
    
    db.commit()
    
    return CreateBulkLotResponse(
        bulk_lot_id=bulk_lot.id,
        message="Bulk lot created successfully"
    )
