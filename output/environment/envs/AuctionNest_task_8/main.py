import os
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, ForeignKey, CheckConstraint, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Auction(Base):
    __tablename__ = "auctions"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(String)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="draft")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    starting_price = Column(Float, nullable=False)
    reserve_price = Column(Float)
    hammer_price = Column(Float)
    status = Column(String, nullable=False, default="draft")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Sale(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False, unique=True)
    buyer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    hammer_price = Column(Float, nullable=False)
    buyer_premium_rate = Column(Float, nullable=False, default=0.2)
    buyer_premium_amount = Column(Float, nullable=False)
    seller_commission_rate = Column(Float, nullable=False, default=0.1)
    seller_commission_amount = Column(Float, nullable=False)
    net_settlement_amount = Column(Float, nullable=False)
    sale_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

app = FastAPI()

class LotSaleDetailsResponse(BaseModel):
    hammer_price: float
    buyer_premium_amount: float
    net_settlement_amount: float

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/auctions/{auction_id}/lots/{lot_id}/sale-details", response_model=LotSaleDetailsResponse, operation_id="get_lot_sale_details")
def get_lot_sale_details(auction_id: int, lot_id: int):
    db = next(get_db())
    
    # Find the lot in the specified auction
    lot = db.query(Lot).filter(Lot.id == lot_id, Lot.auction_id == auction_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found in specified auction")
    
    # Find the corresponding sale record
    sale = db.query(Sale).filter(Sale.lot_id == lot_id).first()
    if not sale:
        raise HTTPException(status_code=404, detail="No sale record found for this lot")
    
    return LotSaleDetailsResponse(
        hammer_price=sale.hammer_price,
        buyer_premium_amount=sale.buyer_premium_amount,
        net_settlement_amount=sale.net_settlement_amount
    )
