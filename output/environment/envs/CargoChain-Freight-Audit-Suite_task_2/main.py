import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    ForeignKey,
    create_engine,
    select,
    case,
)
from sqlalchemy.orm import Session, declarative_base, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
Base = declarative_base()


# --- SQLAlchemy Models ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String)


class Carrier(Base):
    __tablename__ = "carriers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    scac = Column(String, unique=True, nullable=False)


class Shipment(Base):
    __tablename__ = "shipments"
    id = Column(Integer, primary_key=True)
    po_number = Column(String, unique=True, nullable=False)
    origin_zip = Column(String, nullable=False)
    destination_zip = Column(String, nullable=False)
    weight_lbs = Column(Float, nullable=False)
    freight_class = Column(String, nullable=False)


class ParcelTariff(Base):
    __tablename__ = "parcel_tariffs"
    id = Column(Integer, primary_key=True)
    carrier_id = Column(Integer, ForeignKey("carriers.id"), nullable=False)
    service_level = Column(String, nullable=False)
    min_weight_lbs = Column(Float, nullable=False)
    max_weight_lbs = Column(Float, nullable=False)
    rate_per_lb = Column(Float, nullable=False)
    dim_weight_divisor = Column(Float, nullable=False, default=139.0)


class AccessorialCharge(Base):
    __tablename__ = "accessorial_charges"
    id = Column(Integer, primary_key=True)
    carrier_id = Column(Integer, ForeignKey("carriers.id"), nullable=False)
    code = Column(String, nullable=False)
    description = Column(String)
    flat_fee = Column(Float, nullable=False, default=0.0)
    per_lb_fee = Column(Float, nullable=False, default=0.0)


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    invoice_number = Column(String, unique=True, nullable=False)
    carrier_id = Column(Integer, ForeignKey("carriers.id"), nullable=False)
    shipment_id = Column(Integer, ForeignKey("shipments.id"), nullable=False)
    invoice_date = Column(Date, nullable=False)
    billed_weight_lbs = Column(Float, nullable=False)
    billed_amount = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="pending")


class InvoiceAccessorial(Base):
    __tablename__ = "invoice_accessorials"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    accessorial_charge_id = Column(Integer, ForeignKey("accessorial_charges.id"), nullable=False)
    billed_amount = Column(Float, nullable=False)


class AuditResult(Base):
    __tablename__ = "audit_results"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    audit_timestamp = Column(DateTime, default=lambda: __import__('datetime').datetime.utcnow())
    billed_weight_lbs = Column(Float, nullable=False)
    actual_weight_lbs = Column(Float, nullable=False)
    dim_weight_lbs = Column(Float)
    dim_length_in = Column(Float)
    dim_width_in = Column(Float)
    dim_height_in = Column(Float)
    dim_weight_divisor = Column(Float)
    expected_amount = Column(Float, nullable=False)
    billed_amount = Column(Float, nullable=False)
    discrepancy_amount = Column(Float, nullable=False)
    discrepancy_flag = Column(String)
    notes = Column(String)


# --- Pydantic Schemas ---

class InvoiceSchema(BaseModel):
    id: int
    invoice_number: str
    carrier_id: int
    shipment_id: int
    invoice_date: str
    billed_weight_lbs: float
    billed_amount: float
    status: str

class ShipmentSchema(BaseModel):
    id: int
    po_number: str
    origin_zip: str
    destination_zip: str
    weight_lbs: float
    freight_class: str

class CarrierSchema(BaseModel):
    id: int
    name: str
    scac: str

class TariffSchema(BaseModel):
    id: int
    carrier_id: int
    service_level: str
    min_weight_lbs: float
    max_weight_lbs: float
    rate_per_lb: float
    dim_weight_divisor: float

class AccessorialSchema(BaseModel):
    id: int
    code: str
    description: str
    flat_fee: float
    per_lb_fee: float

class GetInvoiceResponse(BaseModel):
    invoice: InvoiceSchema
    shipment: ShipmentSchema
    carrier: CarrierSchema
    tariff: TariffSchema
    accessorials: List[AccessorialSchema]

class DimWeightCheckRequest(BaseModel):
    dim_length_in: float
    dim_width_in: float
    dim_height_in: float

class DimWeightCheckResponse(BaseModel):
    invoice_id: int
    billed_weight_lbs: float
    actual_weight_lbs: float
    dim_weight_lbs: float
    dim_length_in: float
    dim_width_in: float
    dim_height_in: float
    dim_weight_divisor: float
    expected_amount: float
    billed_amount: float
    discrepancy_amount: float
    discrepancy_flag: str
    notes: str


# --- App Setup ---

app = FastAPI(title="CargoChain Freight Audit Suite")

def get_db():
    with Session(engine) as session:
        yield session

@app.get("/audit/invoice/{invoice_number}", operation_id="get_invoice_audit_details")
def get_invoice_audit_details(invoice_number: str):
    with Session(engine) as db:
        # Fetch Invoice
        stmt = select(Invoice).where(Invoice.invoice_number == invoice_number)
        invoice = db.execute(stmt).scalar_one_or_none()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")

        # Fetch Shipment
        stmt = select(Shipment).where(Shipment.id == invoice.shipment_id)
        shipment = db.execute(stmt).scalar_one_or_none()
        
        # Fetch Carrier
        stmt = select(Carrier).where(Carrier.id == invoice.carrier_id)
        carrier = db.execute(stmt).scalar_one_or_none()

        # Fetch Tariff
        # Find the active tariff for this carrier. 
        # Assuming we pick the one that covers the weight or is most relevant.
        # Since no effective date filtering is strictly enforced in schema for query simplicity without context,
        # we'll look for a tariff matching the carrier and weight range.
        stmt = select(ParcelTariff).where(
            ParcelTariff.carrier_id == invoice.carrier_id,
            ParcelTariff.min_weight_lbs <= invoice.billed_weight_lbs,
            ParcelTariff.max_weight_lbs >= invoice.billed_weight_lbs
        )
        tariffs = db.execute(stmt).scalars().all()
        
        if not tariffs:
            # Fallback: just pick any tariff for the carrier if weight range doesn't match exactly
            stmt = select(ParcelTariff).where(ParcelTariff.carrier_id == invoice.carrier_id)
            tariffs = db.execute(stmt).scalars().all()
            
        if not tariffs:
            raise HTTPException(status_code=404, detail="No tariff found for carrier")
            
        # Prefer the first one found (usually GROUND or default)
        tariff = tariffs[0]

        # Fetch Accessorials
        stmt = select(InvoiceAccessorial).where(InvoiceAccessorial.invoice_id == invoice.id)
        inv_accessorial_rows = db.execute(stmt).scalars().all()
        
        accessorials_data = []
        for ia_row in inv_accessorial_rows:
            stmt = select(AccessorialCharge).where(AccessorialCharge.id == ia_row.accessorial_charge_id)
            acc = db.execute(stmt).scalar_one_or_none()
            if acc:
                accessorials_data.append(AccessorialSchema(
                    id=acc.id,
                    code=acc.code,
                    description=acc.description or "",
                    flat_fee=acc.flat_fee,
                    per_lb_fee=acc.per_lb_fee
                ))

        return GetInvoiceResponse(
            invoice=InvoiceSchema(
                id=invoice.id,
                invoice_number=invoice.invoice_number,
                carrier_id=invoice.carrier_id,
                shipment_id=invoice.shipment_id,
                invoice_date=str(invoice.invoice_date),
                billed_weight_lbs=invoice.billed_weight_lbs,
                billed_amount=invoice.billed_amount,
                status=invoice.status
            ),
            shipment=ShipmentSchema(
                id=shipment.id,
                po_number=shipment.po_number,
                origin_zip=shipment.origin_zip,
                destination_zip=shipment.destination_zip,
                weight_lbs=shipment.weight_lbs,
                freight_class=shipment.freight_class
            ),
            carrier=CarrierSchema(
                id=carrier.id,
                name=carrier.name,
                scac=carrier.scac
            ),
            tariff=TariffSchema(
                id=tariff.id,
                carrier_id=tariff.carrier_id,
                service_level=tariff.service_level,
                min_weight_lbs=tariff.min_weight_lbs,
                max_weight_lbs=tariff.max_weight_lbs,
                rate_per_lb=tariff.rate_per_lb,
                dim_weight_divisor=tariff.dim_weight_divisor
            ),
            accessorials=accessorials_data
        )

@app.post("/audit/invoice/{invoice_id}/dimensional-weight-check", operation_id="check_dimensional_weight_discrepancy")
def check_dimensional_weight_discrepancy(invoice_id: int, request: DimWeightCheckRequest):
    with Session(engine) as db:
        stmt = select(Invoice).where(Invoice.id == invoice_id)
        invoice = db.execute(stmt).scalar_one_or_none()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")

        stmt = select(ParcelTariff).where(
            ParcelTariff.carrier_id == invoice.carrier_id,
            ParcelTariff.min_weight_lbs <= invoice.billed_weight_lbs,
            ParcelTariff.max_weight_lbs >= invoice.billed_weight_lbs
        )
        tariffs = db.execute(stmt).scalars().all()
        if not tariffs:
            stmt = select(ParcelTariff).where(ParcelTariff.carrier_id == invoice.carrier_id)
            tariffs = db.execute(stmt).scalars().all()
        
        if not tariffs:
            raise HTTPException(status_code=404, detail="No tariff found for carrier")
            
        tariff = tariffs[0]

        # Calculate Dimensional Weight
        dim_volume = request.dim_length_in * request.dim_width_in * request.dim_height_in
        dim_weight_lbs = dim_volume / tariff.dim_weight_divisor
        
        # Determine Actual Weight for billing purposes (Greater of Actual vs Dim)
        # However, the task says "validate dimensional weight... against contracted parcel tariff".
        # Usually, you bill based on Chargeable Weight = max(Actual, Dim).
        # But here we are checking discrepancy.
        # The prompt implies comparing the calculated dim weight to the billed weight logic.
        # Let's assume the "expected amount" should be based on the chargeable weight.
        
        actual_weight_lbs = invoice.billed_weight_lbs # The weight recorded on the invoice
        
        # Chargeable weight is typically max(actual, dim)
        chargeable_weight = max(actual_weight_lbs, dim_weight_lbs)
        
        # Calculate Expected Amount based on chargeable weight
        expected_amount = chargeable_weight * tariff.rate_per_lb
        
        # Add accessorial fees? The spec doesn't explicitly ask to sum them into expected_amount 
        # for the discrepancy check response structure which only has expected_amount/billed_amount/discrepancy_amount.
        # Usually, base freight is compared. Let's stick to base freight for the core discrepancy unless specified otherwise.
        # If we include accessorials, we'd need to fetch them. The previous endpoint fetched them.
        # To be safe and consistent with "rate adjusted" language which often refers to line item rates:
        
        billed_amount = invoice.billed_amount
        
        discrepancy_amount = abs(expected_amount - billed_amount)
        
        discrepancy_flag = "none"
        notes = ""
        
        # Check if discrepancy exceeds $2.50
        if discrepancy_amount > 2.50:
            discrepancy_flag = "dimensional_weight_recalculated"
            notes = f"dimensional weight recalculated to {dim_weight_lbs:.1f} lbs — rate adjusted"
            
            # Record audit result
            current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
            if not current_user_id:
                 raise HTTPException(status_code=500, detail="User ID not provided")
            
            new_audit = AuditResult(
                invoice_id=invoice_id,
                user_id=int(current_user_id),
                billed_weight_lbs=actual_weight_lbs,
                actual_weight_lbs=actual_weight_lbs,
                dim_weight_lbs=dim_weight_lbs,
                dim_length_in=request.dim_length_in,
                dim_width_in=request.dim_width_in,
                dim_height_in=request.dim_height_in,
                dim_weight_divisor=tariff.dim_weight_divisor,
                expected_amount=expected_amount,
                billed_amount=billed_amount,
                discrepancy_amount=discrepancy_amount,
                discrepancy_flag=discrepancy_flag,
                notes=notes
            )
            db.add(new_audit)
            db.commit()
        else:
            # Even if no flag, we might want to record it? The spec doesn't explicitly say "always record".
            # It says "flag ... if discrepancy exceeds".
            # We will only record if there's a significant finding or if required by broader audit logic.
            # Given the specific instruction "flag ... if", we focus on the flagging.
            pass

        return DimWeightCheckResponse(
            invoice_id=invoice_id,
            billed_weight_lbs=actual_weight_lbs,
            actual_weight_lbs=actual_weight_lbs,
            dim_weight_lbs=dim_weight_lbs,
            dim_length_in=request.dim_length_in,
            dim_width_in=request.dim_width_in,
            dim_height_in=request.dim_height_in,
            dim_weight_divisor=tariff.dim_weight_divisor,
            expected_amount=expected_amount,
            billed_amount=billed_amount,
            discrepancy_amount=discrepancy_amount,
            discrepancy_flag=discrepancy_flag,
            notes=notes
        )
