import os
from datetime import date
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

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
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class Carrier(Base):
    __tablename__ = "carriers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    scac = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class Contract(Base):
    __tablename__ = "contracts"
    id = Column(Integer, primary_key=True, index=True)
    carrier_id = Column(Integer, ForeignKey("carriers.id"), nullable=False)
    contract_number = Column(String, nullable=False)
    effective_date = Column(Date, nullable=False)
    expiration_date = Column(Date)
    status = Column(String, default="active")
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    carrier = relationship("Carrier")
    __table_args__ = (
        CheckConstraint("status IN ('active','inactive','expired')"),
    )

class RateBase(Base):
    __tablename__ = "rate_bases"
    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False)
    origin_zip = Column(String, nullable=False)
    destination_zip = Column(String, nullable=False)
    weight_min = Column(Float, nullable=False)
    weight_max = Column(Float, nullable=False)
    freight_class = Column(String, nullable=False)
    rate_per_cwt = Column(Float, nullable=False)
    min_charge = Column(Float)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    contract = relationship("Contract")
    __table_args__ = (
        CheckConstraint("weight_min <= weight_max"),
    )

class AccessorialCharge(Base):
    __tablename__ = "accessorial_charges"
    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False)
    code = Column(String, nullable=False)
    description = Column(String)
    amount = Column(Float, nullable=False)
    is_percentage = Column(Integer, default=0)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    contract = relationship("Contract")
    __table_args__ = (
        CheckConstraint("is_percentage IN (0,1)"),
        CheckConstraint("code IS NOT NULL"),
    )

class FuelSurcharge(Base):
    __tablename__ = "fuel_surcharges"
    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False)
    effective_date = Column(Date, nullable=False)
    expiration_date = Column(Date)
    surcharge_percentage = Column(Float, nullable=False)
    cap_percentage = Column(Float)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    contract = relationship("Contract")

class Shipment(Base):
    __tablename__ = "shipments"
    id = Column(Integer, primary_key=True, index=True)
    po_number = Column(String, unique=True, nullable=False)
    origin_zip = Column(String, nullable=False)
    destination_zip = Column(String, nullable=False)
    weight_lbs = Column(Float, nullable=False)
    freight_class = Column(String, nullable=False)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String, unique=True, nullable=False)
    carrier_id = Column(Integer, ForeignKey("carriers.id"), nullable=False)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False)
    contract_id = Column(Integer, ForeignKey("contracts.id"))
    invoice_date = Column(Date, nullable=False)
    total_amount = Column(Float, nullable=False)
    fuel_surcharge_amount = Column(Float)
    fuel_surcharge_percentage = Column(Float)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    carrier = relationship("Carrier")
    shipment = relationship("Shipment")
    contract = relationship("Contract")
    __table_args__ = (
        CheckConstraint("status IN ('pending','audited','paid','disputed')"),
    )

class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    line_number = Column(Integer, nullable=False)
    description = Column(String, nullable=False)
    charge_type = Column(String)
    amount = Column(Float, nullable=False)
    accessorial_code = Column(String)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    invoice = relationship("Invoice")
    __table_args__ = (
        CheckConstraint("charge_type IN ('freight','fuel','accessorial','other')"),
    )

class AuditException(Base):
    __tablename__ = "audit_exceptions"
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    line_item_id = Column(Integer, ForeignKey("invoice_line_items.id"))
    exception_code = Column(String, nullable=False)
    exception_description = Column(String, nullable=False)
    expected_value = Column(Float)
    actual_value = Column(Float)
    severity = Column(String, default="warning")
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    invoice = relationship("Invoice")
    line_item = relationship("InvoiceLineItem")
    __table_args__ = (
        CheckConstraint("severity IN ('info','warning','error','critical')"),
    )

# Pydantic models
class ExceptionDetail(BaseModel):
    exception_code: str
    exception_description: str
    expected_value: float
    actual_value: float
    severity: str
    line_item_id: Optional[int]

class LineItem(BaseModel):
    line_number: int
    description: str
    charge_type: str
    amount: float
    accessorial_code: Optional[str]

class AuditResult(BaseModel):
    invoice_number: str
    carrier_name: str
    shipment_po: str
    origin_zip: str
    destination_zip: str
    weight_lbs: float
    freight_class: str
    contract_number: str
    audit_date: date
    total_amount: float
    fuel_surcharge_amount: float
    fuel_surcharge_percentage: float
    exceptions: List[ExceptionDetail]
    line_items: List[LineItem]

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/audit/invoice/{invoice_number}", response_model=AuditResult, operation_id="audit_invoice")
def audit_invoice(invoice_number: str, db = Depends(get_db)):
    # Get invoice
    invoice = db.query(Invoice).filter(Invoice.invoice_number == invoice_number).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Get shipment details
    shipment = invoice.shipment
    
    # Get carrier details
    carrier = invoice.carrier
    
    # Get contract details
    contract = invoice.contract
    
    # Get active fuel surcharge
    fuel_surcharge = db.query(FuelSurcharge).filter(
        FuelSurcharge.contract_id == contract.id,
        FuelSurcharge.effective_date <= invoice.invoice_date,
        (FuelSurcharge.expiration_date.is_(None) | (FuelSurcharge.expiration_date >= invoice.invoice_date))
    ).first()
    
    # Calculate expected freight charge
    rate_base = db.query(RateBase).filter(
        RateBase.contract_id == contract.id,
        RateBase.origin_zip == shipment.origin_zip,
        RateBase.destination_zip == shipment.destination_zip,
        RateBase.freight_class == shipment.freight_class,
        RateBase.weight_min <= shipment.weight_lbs,
        RateBase.weight_max >= shipment.weight_lbs
    ).first()
    
    expected_freight_charge = None
    if rate_base:
        cwt = shipment.weight_lbs / 100
        expected_freight_charge = max(rate_base.min_charge or 0, cwt * rate_base.rate_per_cwt)
    
    # Get accessorial charges
    accessorial_charges = {ac.code: ac for ac in db.query(AccessorialCharge).filter(AccessorialCharge.contract_id == contract.id)}
    
    # Get line items
    line_items = db.query(InvoiceLineItem).filter(InvoiceLineItem.invoice_id == invoice.id).order_by(InvoiceLineItem.line_number).all()
    
    # Initialize exceptions
    exceptions = []
    
    # Check fuel surcharge percentage
    if fuel_surcharge and fuel_surcharge.cap_percentage:
        if invoice.fuel_surcharge_percentage > fuel_surcharge.cap_percentage:
            exceptions.append(AuditException(
                invoice_id=invoice.id,
                exception_code="FUEL_SURCHARGE_CAP_EXCEEDED",
                exception_description="Fuel surcharge exceeds 12.5% cap",
                expected_value=fuel_surcharge.cap_percentage,
                actual_value=invoice.fuel_surcharge_percentage,
                severity="error"
            ))
    
    # Check freight charge
    if expected_freight_charge is not None:
        freight_line_item = next((li for li in line_items if li.charge_type == "freight"), None)
        if freight_line_item:
            if abs(freight_line_item.amount - expected_freight_charge) > 0.01:
                exceptions.append(AuditException(
                    invoice_id=invoice.id,
                    line_item_id=freight_line_item.id,
                    exception_code="FREIGHT_CHARGE_MISMATCH",
                    exception_description="Freight charge does not match expected rate",
                    expected_value=expected_freight_charge,
                    actual_value=freight_line_item.amount,
                    severity="warning"
                ))
    
    # Check accessorial charges
    for line_item in line_items:
        if line_item.charge_type == "accessorial" and line_item.accessorial_code:
            accessorial = accessorial_charges.get(line_item.accessorial_code)
            if accessorial:
                if abs(line_item.amount - accessorial.amount) > 0.01:
                    exceptions.append(AuditException(
                        invoice_id=invoice.id,
                        line_item_id=line_item.id,
                        exception_code="ACCESSORIAL_CHARGE_MISMATCH",
                        exception_description="Accessorial charge does not match contract rate",
                        expected_value=accessorial.amount,
                        actual_value=line_item.amount,
                        severity="warning"
                    ))
    
    # Convert exceptions to response format
    exception_details = [
        ExceptionDetail(
            exception_code=e.exception_code,
            exception_description=e.exception_description,
            expected_value=e.expected_value,
            actual_value=e.actual_value,
            severity=e.severity,
            line_item_id=e.line_item_id
        ) for e in exceptions
    ]
    
    # Convert line items to response format
    line_item_details = [
        LineItem(
            line_number=li.line_number,
            description=li.description,
            charge_type=li.charge_type,
            amount=li.amount,
            accessorial_code=li.accessorial_code
        ) for li in line_items
    ]
    
    # Return audit result
    return AuditResult(
        invoice_number=invoice.invoice_number,
        carrier_name=carrier.name,
        shipment_po=shipment.po_number,
        origin_zip=shipment.origin_zip,
        destination_zip=shipment.destination_zip,
        weight_lbs=shipment.weight_lbs,
        freight_class=shipment.freight_class,
        contract_number=contract.contract_number,
        audit_date=date.today(),
        total_amount=invoice.total_amount,
        fuel_surcharge_amount=invoice.fuel_surcharge_amount,
        fuel_surcharge_percentage=invoice.fuel_surcharge_percentage,
        exceptions=exception_details,
        line_items=line_item_details
    )
