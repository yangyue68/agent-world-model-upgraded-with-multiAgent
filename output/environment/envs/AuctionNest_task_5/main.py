import os
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String, unique=True, nullable=False)
    user_id = Column(Integer, nullable=False)
    shipping_address = Column(String)
    shipping_city = Column(String)
    shipping_state = Column(String)
    shipping_zip = Column(String)
    shipping_method = Column(String)
    payment_status = Column(String, default="pending")
    payment_method = Column(String)
    total_amount = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, nullable=False)
    payment_method = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, default="pending")
    transaction_id = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ShippingLabel(Base):
    __tablename__ = "shipping_labels"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, nullable=False)
    carrier = Column(String, nullable=False)
    tracking_number = Column(String, unique=True)
    label_url = Column(String)
    status = Column(String, default="created")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


app = FastAPI()


class UpdateInvoiceRequest(BaseModel):
    shipping_address: str
    shipping_city: str
    shipping_state: str
    shipping_zip: str
    shipping_method: Literal["standard", "express", "overnight"]
    payment_method: Literal["paypal", "credit_card"]


class GenerateShippingLabelResponse(BaseModel):
    label_id: int
    tracking_number: str
    label_url: str
    message: str


class UpdateInvoiceResponse(BaseModel):
    invoice_id: int
    payment_id: int
    message: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/invoices/by-number/{invoice_number}", response_model=dict, operation_id="get_invoice_by_number")
def get_invoice_by_number(
    invoice_number: str = Path(..., description="The invoice number (e.g., INV-7891)")
):
    db = next(get_db())
    invoice = (
        db.query(Invoice)
        .filter(Invoice.invoice_number == invoice_number)
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "user_id": invoice.user_id,
        "shipping_address": invoice.shipping_address,
        "shipping_city": invoice.shipping_city,
        "shipping_state": invoice.shipping_state,
        "shipping_zip": invoice.shipping_zip,
        "shipping_method": invoice.shipping_method,
        "payment_status": invoice.payment_status,
        "payment_method": invoice.payment_method,
        "total_amount": invoice.total_amount,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "updated_at": invoice.updated_at.isoformat() if invoice.updated_at else None,
    }


@app.put("/invoices/{invoice_id}", response_model=UpdateInvoiceResponse, operation_id="update_invoice_shipping_and_payment")
def update_invoice_shipping_and_payment(
    invoice_id: str,
    update_data: UpdateInvoiceRequest,
):
    db = next(get_db())
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Update shipping information
    invoice.shipping_address = update_data.shipping_address
    invoice.shipping_city = update_data.shipping_city
    invoice.shipping_state = update_data.shipping_state
    invoice.shipping_zip = update_data.shipping_zip
    invoice.shipping_method = update_data.shipping_method
    invoice.payment_method = update_data.payment_method
    invoice.updated_at = datetime.utcnow()

    # Create payment record
    payment = Payment(
        invoice_id=invoice.id,
        payment_method=update_data.payment_method,
        amount=invoice.total_amount,
        status="completed",
        transaction_id=f"TXN-{update_data.payment_method.upper()}-{invoice.id}",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    # Update payment status
    invoice.payment_status = "completed"
    db.commit()
    db.refresh(invoice)

    return {
        "invoice_id": invoice.id,
        "payment_id": payment.id,
        "message": "Invoice updated successfully",
    }


@app.post("/invoices/{invoice_id}/shipping_label", response_model=GenerateShippingLabelResponse, operation_id="generate_shipping_label")
def generate_shipping_label(invoice_id: str):
    db = next(get_db())
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Simulate FedEx API integration
    tracking_number = f"FDX{invoice.id:09d}US"
    label_url = f"https://labels.example.com/fedex/{tracking_number}.pdf"

    # Create shipping label record
    shipping_label = ShippingLabel(
        invoice_id=invoice.id,
        carrier="FedEx",
        tracking_number=tracking_number,
        label_url=label_url,
        status="created",
    )
    db.add(shipping_label)
    db.commit()
    db.refresh(shipping_label)

    return {
        "label_id": shipping_label.id,
        "tracking_number": tracking_number,
        "label_url": label_url,
        "message": "Shipping label generated successfully",
    }
