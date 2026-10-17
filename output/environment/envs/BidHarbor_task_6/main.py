import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, text, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Auction(Base):
    __tablename__ = "auctions"
    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    starting_bid = Column(Float, nullable=False)
    current_bid = Column(Float)
    winner_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="open")
    closed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    bidder_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), unique=True, nullable=False)
    buyer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    winning_bid = Column(Float, nullable=False)
    buyer_premium_rate = Column(Float, default=0.10)
    buyer_premium_amount = Column(Float, nullable=False)
    sales_tax_rate = Column(Float, default=0.08)
    sales_tax_amount = Column(Float, nullable=False)
    shipping_amount = Column(Float, default=50.00)
    total_amount = Column(Float, nullable=False)
    status = Column(String, default="draft")
    sent_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    description = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class InvoiceResponse(BaseModel):
    id: int
    auction_id: int
    buyer_id: int
    seller_id: int
    winning_bid: float
    buyer_premium_rate: float
    buyer_premium_amount: float
    sales_tax_rate: float
    sales_tax_amount: float
    shipping_amount: float
    total_amount: float
    status: str
    sent_at: Optional[str]
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

@app.post("/auctions/{auction_id}/generate-invoice", response_model=InvoiceResponse, operation_id="generate_auction_invoice")
def generate_auction_invoice(auction_id: int, db: Session = Depends(get_db)):
    # Get the auction
    auction = db.query(Auction).filter(Auction.id == auction_id).first()
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")
    
    if auction.status != "closed":
        raise HTTPException(status_code=400, detail="Auction must be closed to generate invoice")
    
    if not auction.winner_id:
        raise HTTPException(status_code=400, detail="Auction has no winner")
    
    # Check if invoice already exists
    existing_invoice = db.query(Invoice).filter(Invoice.auction_id == auction_id).first()
    if existing_invoice:
        raise HTTPException(status_code=400, detail="Invoice already exists for this auction")
    
    # Calculate amounts
    winning_bid = auction.current_bid
    buyer_premium_rate = 0.10
    buyer_premium_amount = winning_bid * buyer_premium_rate
    
    sales_tax_rate = 0.08
    sales_tax_amount = (winning_bid + buyer_premium_amount) * sales_tax_rate
    
    shipping_amount = 50.00
    total_amount = winning_bid + buyer_premium_amount + sales_tax_amount + shipping_amount
    
    # Create invoice
    invoice = Invoice(
        auction_id=auction_id,
        buyer_id=auction.winner_id,
        seller_id=auction.seller_id,
        winning_bid=winning_bid,
        buyer_premium_rate=buyer_premium_rate,
        buyer_premium_amount=buyer_premium_amount,
        sales_tax_rate=sales_tax_rate,
        sales_tax_amount=sales_tax_amount,
        shipping_amount=shipping_amount,
        total_amount=total_amount,
        status="draft"
    )
    
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    
    # Create invoice items
    items = [
        InvoiceItem(invoice_id=invoice.id, description=f"Winning bid: {auction.title}", amount=winning_bid),
        InvoiceItem(invoice_id=invoice.id, description=f"Buyer's premium ({buyer_premium_rate*100}%)", amount=buyer_premium_amount),
        InvoiceItem(invoice_id=invoice.id, description=f"Sales tax ({sales_tax_rate*100}%)", amount=sales_tax_amount),
        InvoiceItem(invoice_id=invoice.id, description="Shipping and handling", amount=shipping_amount)
    ]
    
    for item in items:
        db.add(item)
    
    db.commit()
    
    # Convert sent_at to string if it's None
    sent_at_str = invoice.sent_at.isoformat() if invoice.sent_at else None
    
    return {
        "id": invoice.id,
        "auction_id": invoice.auction_id,
        "buyer_id": invoice.buyer_id,
        "seller_id": invoice.seller_id,
        "winning_bid": invoice.winning_bid,
        "buyer_premium_rate": invoice.buyer_premium_rate,
        "buyer_premium_amount": invoice.buyer_premium_amount,
        "sales_tax_rate": invoice.sales_tax_rate,
        "sales_tax_amount": invoice.sales_tax_amount,
        "shipping_amount": invoice.shipping_amount,
        "total_amount": invoice.total_amount,
        "status": invoice.status,
        "sent_at": sent_at_str,
        "created_at": invoice.created_at.isoformat(),
        "updated_at": invoice.updated_at.isoformat()
    }

@app.post("/invoices/{invoice_id}/send", response_model=InvoiceResponse, operation_id="send_invoice")
def send_invoice(invoice_id: int, db: Session = Depends(get_db)):
    # Get the invoice
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Update invoice status to sent and record sent_at timestamp
    invoice.status = "sent"
    invoice.sent_at = datetime.utcnow()
    invoice.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(invoice)
    
    # Convert sent_at to string if it's None
    sent_at_str = invoice.sent_at.isoformat() if invoice.sent_at else None
    
    return {
        "id": invoice.id,
        "auction_id": invoice.auction_id,
        "buyer_id": invoice.buyer_id,
        "seller_id": invoice.seller_id,
        "winning_bid": invoice.winning_bid,
        "buyer_premium_rate": invoice.buyer_premium_rate,
        "buyer_premium_amount": invoice.buyer_premium_amount,
        "sales_tax_rate": invoice.sales_tax_rate,
        "sales_tax_amount": invoice.sales_tax_amount,
        "shipping_amount": invoice.shipping_amount,
        "total_amount": invoice.total_amount,
        "status": invoice.status,
        "sent_at": sent_at_str,
        "created_at": invoice.created_at.isoformat(),
        "updated_at": invoice.updated_at.isoformat()
    }
