import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
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

class Lot(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    marketplace_id = Column(Integer, ForeignKey("marketplaces.id"), nullable=False)
    title = Column(String, nullable=False)
    condition = Column(String, nullable=False)
    retail_value = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String, default="draft")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ManifestItem(Base):
    __tablename__ = "manifest_items"
    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    sku = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    condition = Column(String, nullable=False)
    retail_value = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class AuctionTerm(Base):
    __tablename__ = "auction_terms"
    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), unique=True, nullable=False)
    starting_bid = Column(Float, nullable=False)
    reserve_price = Column(Float)
    bid_increment = Column(Float, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    shipping_cost = Column(Float, nullable=False)
    shipping_method = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class LotCreate(BaseModel):
    marketplace_id: int
    title: str
    condition: str
    retail_value: float
    quantity: int

class ManifestItemCreate(BaseModel):
    sku: str
    quantity: int
    condition: str
    retail_value: float

class ManifestUpload(BaseModel):
    manifest_items: List[ManifestItemCreate]

class AuctionTermsCreate(BaseModel):
    starting_bid: float
    reserve_price: Optional[float] = None
    bid_increment: float
    start_date: str
    end_date: str
    shipping_cost: float
    shipping_method: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/lots", response_model=dict, operation_id="create_lot_listing")
def create_lot_listing(lot: LotCreate, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    db_lot = Lot(
        user_id=current_user_id,
        marketplace_id=lot.marketplace_id,
        title=lot.title,
        condition=lot.condition,
        retail_value=lot.retail_value,
        quantity=lot.quantity
    )
    db.add(db_lot)
    db.commit()
    db.refresh(db_lot)
    
    return {
        "id": db_lot.id,
        "user_id": db_lot.user_id,
        "marketplace_id": db_lot.marketplace_id,
        "title": db_lot.title,
        "condition": db_lot.condition,
        "retail_value": db_lot.retail_value,
        "quantity": db_lot.quantity,
        "status": db_lot.status
    }

@app.post("/lots/{lot_id}/manifest", response_model=dict, operation_id="upload_lot_manifest")
def upload_lot_manifest(lot_id: int, manifest: ManifestUpload, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify lot exists and belongs to current user
    db_lot = db.query(Lot).filter(Lot.id == lot_id, Lot.user_id == current_user_id).first()
    if not db_lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    
    for item in manifest.manifest_items:
        db_item = ManifestItem(
            lot_id=lot_id,
            sku=item.sku,
            quantity=item.quantity,
            condition=item.condition,
            retail_value=item.retail_value
        )
        db.add(db_item)
    
    db.commit()
    
    return {"message": "Manifest uploaded successfully"}

@app.post("/lots/{lot_id}/auction_terms", response_model=dict, operation_id="set_auction_terms")
def set_auction_terms(lot_id: int, auction_terms: AuctionTermsCreate, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify lot exists and belongs to current user
    db_lot = db.query(Lot).filter(Lot.id == lot_id, Lot.user_id == current_user_id).first()
    if not db_lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    
    # Parse dates
    start_date = datetime.fromisoformat(auction_terms.start_date.replace('Z', '+00:00'))
    end_date = datetime.fromisoformat(auction_terms.end_date.replace('Z', '+00:00'))
    
    db_auction_term = AuctionTerm(
        lot_id=lot_id,
        starting_bid=auction_terms.starting_bid,
        reserve_price=auction_terms.reserve_price,
        bid_increment=auction_terms.bid_increment,
        start_date=start_date,
        end_date=end_date,
        shipping_cost=auction_terms.shipping_cost,
        shipping_method=auction_terms.shipping_method
    )
    db.add(db_auction_term)
    db.commit()
    
    return {"message": "Auction terms set successfully"}
