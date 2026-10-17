import os
from datetime import datetime
from typing import Literal
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PaymentMethod(Base):
    __tablename__ = "payment_methods"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    method_type = Column(String, nullable=False)
    last_four = Column(String)
    is_default = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    CheckConstraint("is_default IN (0, 1)", name="check_is_default")

class Auction(Base):
    __tablename__ = "auctions"
    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    starting_price = Column(Float, nullable=False)
    current_price = Column(Float)
    end_time = Column(DateTime, nullable=False)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    CheckConstraint("status IN ('active', 'ended', 'cancelled')", name="check_status")

class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    bidder_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String, unique=True, nullable=False)
    auction_id = Column(Integer, ForeignKey("auctions.id"), nullable=False)
    buyer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    winning_bid_id = Column(Integer, ForeignKey("bids.id"), nullable=False)
    total_amount = Column(Float, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    CheckConstraint("status IN ('pending', 'paid', 'shipped', 'delivered', 'cancelled')", name="check_status")

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    payment_method_id = Column(Integer, ForeignKey("payment_methods.id"), nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, default="pending")
    transaction_id = Column(String, unique=True)
    processed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    CheckConstraint("status IN ('pending', 'completed', 'failed', 'refunded')", name="check_status")

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String, unique=True, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, unique=True)
    payment_id = Column(Integer, ForeignKey("payments.id"))
    amount = Column(Float, nullable=False)
    due_date = Column(DateTime, nullable=False)
    status = Column(String, default="unpaid")
    issued_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    CheckConstraint("status IN ('unpaid', 'paid', 'overdue', 'cancelled')", name="check_status")

# Pydantic models
class ProcessPaymentResponse(BaseModel):
    payment_id: int
    order_id: str
    status: Literal["pending", "completed", "failed", "refunded"]
    amount: float
    transaction_id: str

class InvoiceDetailsResponse(BaseModel):
    invoice_number: str
    due_date: datetime
    amount: float
    status: Literal["unpaid", "paid", "overdue", "cancelled"]

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/orders/{order_id}/process-payment", response_model=ProcessPaymentResponse, operation_id="process_order_payment")
def process_order_payment(order_id: str):
    db = next(get_db())
    
    # Get the order
    order = db.query(Order).filter(Order.order_number == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Get the current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the order belongs to the current user
    if order.buyer_id != current_user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Get the user's default payment method
    payment_method = db.query(PaymentMethod).filter(
        PaymentMethod.user_id == current_user_id,
        PaymentMethod.is_default == 1
    ).first()
    
    if not payment_method:
        raise HTTPException(status_code=400, detail="No default payment method found")
    
    # Create a new payment
    payment = Payment(
        order_id=order.id,
        payment_method_id=payment_method.id,
        amount=order.total_amount,
        status="completed",
        transaction_id=f"TXN-{datetime.now().strftime('%Y%m%d')}-{order.id:04d}",
        processed_at=datetime.utcnow()
    )
    
    db.add(payment)
    db.commit()
    db.refresh(payment)
    
    # Update order status
    order.status = "paid"
    db.commit()
    
    # Create invoice if it doesn't exist
    invoice = db.query(Invoice).filter(Invoice.order_id == order.id).first()
    if not invoice:
        invoice = Invoice(
            invoice_number=f"INV-{datetime.now().strftime('%Y-%m-%d')}-{order.id:04d}",
            order_id=order.id,
            payment_id=payment.id,
            amount=order.total_amount,
            due_date=datetime.utcnow(),
            status="paid"
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
    
    return {
        "payment_id": payment.id,
        "order_id": order.order_number,
        "status": payment.status,
        "amount": payment.amount,
        "transaction_id": payment.transaction_id
    }

@app.get("/invoices/order/{order_id}", response_model=InvoiceDetailsResponse, operation_id="get_invoice_details")
def get_invoice_details(order_id: str):
    db = next(get_db())
    
    # Get the order
    order = db.query(Order).filter(Order.order_number == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Get the current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the order belongs to the current user
    if order.buyer_id != current_user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Get the invoice for the order
    invoice = db.query(Invoice).filter(Invoice.order_id == order.id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    return {
        "invoice_number": invoice.invoice_number,
        "due_date": invoice.due_date,
        "amount": invoice.amount,
        "status": invoice.status
    }
