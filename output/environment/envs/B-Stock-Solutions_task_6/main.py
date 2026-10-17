import os
from datetime import datetime
from typing import List, Literal
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, CheckConstraint, case
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
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Auction(Base):
    __tablename__ = "auctions"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    starting_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    category = relationship("Category")
    seller = relationship("User")

class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    bidder_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    bid_time = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    auction = relationship("Auction")
    bidder = relationship("User")

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False, unique=True)
    buyer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, default="Unpaid")
    due_date = Column(DateTime)
    paid_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    auction = relationship("Auction")
    buyer = relationship("User", foreign_keys=[buyer_id])
    seller = relationship("User", foreign_keys=[seller_id])

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    payer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    payment_method = Column(String, nullable=False)
    transaction_id = Column(String, unique=True)
    status = Column(String, default="completed")
    paid_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    invoice = relationship("Invoice")
    payer = relationship("User")

# Pydantic Models
class AuctionResponse(BaseModel):
    auction_id: int
    title: str
    description: str
    category_id: int
    seller_id: int
    buyer_id: int
    final_price: float
    end_time: datetime

class InvoicePaymentRequest(BaseModel):
    category_name: str
    payment_method: Literal["credit_card", "debit_card", "bank_transfer", "paypal"]
    payer_id: int

class InvoiceProcessed(BaseModel):
    invoice_id: int
    auction_id: int
    amount: float
    status: str

class InvoicePaymentResponse(BaseModel):
    processed_invoices: List[InvoiceProcessed]
    total_processed: int

class InvoiceStatusVerification(BaseModel):
    invoice_id: int
    auction_id: int
    buyer_id: int
    seller_id: int
    amount: float
    status: str
    paid_at: datetime

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/auctions/won_by_category", response_model=List[AuctionResponse], operation_id="find_won_auctions_by_category")
def find_won_auctions_by_category(category_name: str):
    db = next(get_db())
    
    # Get the category ID
    category = db.query(Category).filter(Category.name == category_name).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Find ended auctions in this category with highest bids as winning bids
    # This query gets auctions that have ended and have at least one bid
    # The winning bid is the highest bid for each auction
    auctions = db.query(Auction) \
        .filter(
            Auction.category_id == category.id,
            Auction.status == "ended"
        ) \
        .all()
    
    result = []
    for auction in auctions:
        # Find the winning bid (highest bid)
        winning_bid = db.query(Bid) \
            .filter(Bid.auction_id == auction.id) \
            .order_by(Bid.amount.desc()) \
            .first()
        
        if winning_bid:
            result.append(AuctionResponse(
                auction_id=auction.id,
                title=auction.title,
                description=auction.description,
                category_id=auction.category_id,
                seller_id=auction.seller_id,
                buyer_id=winning_bid.bidder_id,
                final_price=winning_bid.amount,
                end_time=auction.end_time
            ))
    
    return result

@app.post("/invoices/pay_multiple", response_model=InvoicePaymentResponse, operation_id="pay_outstanding_invoices_for_auctions")
def pay_outstanding_invoices_for_auctions(request: InvoicePaymentRequest):
    db = next(get_db())
    
    # Get the category ID
    category = db.query(Category).filter(Category.name == request.category_name).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Get all ended auctions in this category
    ended_auctions = db.query(Auction) \
        .filter(
            Auction.category_id == category.id,
            Auction.status == "ended"
        ) \
        .all()
    
    # Get invoices for these auctions that are unpaid
    unpaid_invoices = db.query(Invoice) \
        .filter(
            Invoice.auction_id.in_([a.id for a in ended_auctions]),
            Invoice.status == "Unpaid"
        ) \
        .all()
    
    processed_invoices = []
    
    # Process each unpaid invoice
    for invoice in unpaid_invoices:
        # Create a payment record
        payment = Payment(
            invoice_id=invoice.id,
            payer_id=request.payer_id,
            amount=invoice.amount,
            payment_method=request.payment_method,
            status="completed",
            paid_at=datetime.utcnow()
        )
        
        db.add(payment)
        
        # Update invoice status
        invoice.status = "Paid"
        invoice.paid_at = datetime.utcnow()
        
        # Add to processed list
        processed_invoices.append(InvoiceProcessed(
            invoice_id=invoice.id,
            auction_id=invoice.auction_id,
            amount=invoice.amount,
            status="Paid"
        ))
    
    db.commit()
    
    return InvoicePaymentResponse(
        processed_invoices=processed_invoices,
        total_processed=len(processed_invoices)
    )

@app.get("/invoices/status_verification", response_model=List[InvoiceStatusVerification], operation_id="verify_invoice_payment_status")
def verify_invoice_payment_status(category_name: str):
    db = next(get_db())
    
    # Get the category ID
    category = db.query(Category).filter(Category.name == category_name).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Get all ended auctions in this category
    ended_auctions = db.query(Auction) \
        .filter(
            Auction.category_id == category.id,
            Auction.status == "ended"
        ) \
        .all()
    
    # Get invoices for these auctions that are now paid
    paid_invoices = db.query(Invoice) \
        .filter(
            Invoice.auction_id.in_([a.id for a in ended_auctions]),
            Invoice.status == "Paid"
        ) \
        .all()
    
    result = []
    for invoice in paid_invoices:
        result.append(InvoiceStatusVerification(
            invoice_id=invoice.id,
            auction_id=invoice.auction_id,
            buyer_id=invoice.buyer_id,
            seller_id=invoice.seller_id,
            amount=invoice.amount,
            status=invoice.status,
            paid_at=invoice.paid_at
        ))
    
    return result
