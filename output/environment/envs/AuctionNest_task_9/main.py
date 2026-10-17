import os
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    func,
    select,
    text,
    case,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
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
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Consignor(Base):
    __tablename__ = "consignors"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    address = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Buyer(Base):
    __tablename__ = "buyers"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String)
    address = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    consignor_id = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    category = Column(String)
    condition = Column(String)
    status = Column(String, default="consigned")
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String, unique=True, nullable=False)
    item_id = Column(Integer, nullable=False)
    buyer_id = Column(Integer, nullable=False)
    sale_price = Column(Float, nullable=False)
    commission_rate = Column(Float, nullable=False)
    commission_amount = Column(Float, nullable=False)
    tax_rate = Column(Float, nullable=False)
    tax_amount = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    status = Column(String, default="draft")
    sent_at = Column(DateTime)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class InvoiceEmail(Base):
    __tablename__ = "invoice_emails"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, nullable=False)
    recipient_email = Column(String, nullable=False)
    sent_at = Column(DateTime, default=func.current_timestamp())
    status = Column(String, default="sent")
    error_message = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI()


class InvoiceResponse(BaseModel):
    id: int
    invoice_number: str
    status: Literal["draft", "sent", "paid", "cancelled"]
    total_amount: float


class SendInvoiceResponse(BaseModel):
    invoice_id: int
    status: Literal["draft", "sent", "paid", "cancelled"]
    sent_at: str


@app.post("/invoices", response_model=InvoiceResponse, operation_id="create_private_sale_invoice")
def create_private_sale_invoice(
    item_title: str = Query(...),
    buyer_email: str = Query(...),
    sale_price: float = Query(...),
    commission_rate: float = Query(...),
    tax_rate: float = Query(...),
    db=None,
):
    if db is None:
        db = next(get_db())

    # Validate inputs
    if sale_price < 0:
        raise HTTPException(status_code=400, detail="Sale price must be non-negative")
    if not (0 <= commission_rate <= 1):
        raise HTTPException(status_code=400, detail="Commission rate must be between 0 and 1")
    if not (0 <= tax_rate <= 1):
        raise HTTPException(status_code=400, detail="Tax rate must be between 0 and 1")

    # Find the item by title
    item = db.execute(select(Item).where(Item.title == item_title)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    # Find the buyer by email
    buyer = db.execute(select(Buyer).where(Buyer.email == buyer_email)).scalar_one_or_none()
    if not buyer:
        raise HTTPException(status_code=404, detail="Buyer not found")

    # Calculate amounts
    commission_amount = round(sale_price * commission_rate, 2)
    tax_amount = round(sale_price * tax_rate, 2)
    total_amount = round(sale_price + commission_amount + tax_amount, 2)

    # Generate invoice number
    last_invoice = db.execute(
        select(func.max(Invoice.invoice_number))
    ).scalar_one_or_none()
    if last_invoice:
        invoice_num = int(last_invoice.split("-")[-1]) + 1
    else:
        invoice_num = 1
    invoice_number = f"INV-2024-{invoice_num:04d}"

    # Create invoice
    invoice = Invoice(
        invoice_number=invoice_number,
        item_id=item.id,
        buyer_id=buyer.id,
        sale_price=sale_price,
        commission_rate=commission_rate,
        commission_amount=commission_amount,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        total_amount=total_amount,
        status="draft",
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    return InvoiceResponse(
        id=invoice.id,
        invoice_number=invoice.invoice_number,
        status=invoice.status,
        total_amount=invoice.total_amount,
    )


@app.post("/invoices/{invoice_id}/send", response_model=SendInvoiceResponse, operation_id="send_invoice")
def send_invoice(
    invoice_id: int,
    db=None,
):
    if db is None:
        db = next(get_db())

    # Get the invoice
    invoice = db.execute(select(Invoice).where(Invoice.id == invoice_id)).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Get buyer email
    buyer = db.execute(select(Buyer).where(Buyer.id == invoice.buyer_id)).scalar_one_or_none()
    if not buyer:
        raise HTTPException(status_code=404, detail="Buyer not found")

    # Update invoice status and sent_at
    current_time = datetime.utcnow()
    invoice.status = "sent"
    invoice.sent_at = current_time
    db.commit()
    db.refresh(invoice)

    # Record email sent
    email_record = InvoiceEmail(
        invoice_id=invoice.id,
        recipient_email=buyer.email,
        sent_at=current_time,
        status="sent",
    )
    db.add(email_record)
    db.commit()

    return SendInvoiceResponse(
        invoice_id=invoice.id,
        status=invoice.status,
        sent_at=current_time.isoformat() + "Z",
    )
