import os
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Date,
    Integer,
    String,
    REAL,
    TEXT,
    ForeignKey,
    CheckConstraint,
    case,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.sql import func

DATABASE_PATH = os.environ.get("DATABASE_PATH")

Base = declarative_base()

# Define models based on schema
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class Supplier(Base):
    __tablename__ = 'suppliers'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    contact_name = Column(String)
    email = Column(String)
    phone = Column(String)
    address = Column(String)
    lead_time_days = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class Part(Base):
    __tablename__ = 'parts'
    id = Column(Integer, primary_key=True)
    part_number = Column(String, unique=True, nullable=False)
    description = Column(String)
    unit_price = Column(REAL, nullable=False, default=0.0)
    stock_quantity = Column(Integer, nullable=False, default=0)
    reserved_quantity = Column(Integer, nullable=False, default=0)
    reorder_level = Column(Integer, nullable=False, default=0)
    supplier_id = Column(Integer, ForeignKey('suppliers.id', ondelete='SET NULL'))
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class PurchaseOrder(Base):
    __tablename__ = 'purchase_orders'
    id = Column(Integer, primary_key=True)
    po_number = Column(String, unique=True, nullable=False)
    supplier_id = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default='draft',
                    server_default="draft",
                    info={'check': "status IN ('draft','submitted','confirmed','partially_received','received','cancelled','closed')"})
    order_date = Column(Date, nullable=False)
    expected_delivery_date = Column(Date)
    actual_delivery_date = Column(Date)
    total_amount = Column(REAL, nullable=False, default=0.0)
    notes = Column(String)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class PurchaseOrderLine(Base):
    __tablename__ = 'purchase_order_lines'
    id = Column(Integer, primary_key=True)
    purchase_order_id = Column(Integer, nullable=False)
    part_id = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False, info={'check': "quantity > 0"})
    unit_price = Column(REAL, nullable=False, default=0.0)
    received_quantity = Column(Integer, nullable=False, default=0)
    line_status = Column(String, nullable=False, default='open',
                         info={'check': "line_status IN ('open','partially_received','received','cancelled')"})
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class ServiceOrder(Base):
    __tablename__ = 'service_orders'
    id = Column(Integer, primary_key=True)
    so_number = Column(String, unique=True, nullable=False)
    customer_name = Column(String)
    customer_contact = Column(String)
    status = Column(String, nullable=False, default='pending',
                    info={'check': "status IN ('pending','in_progress','completed','cancelled')"})
    priority = Column(Integer, nullable=False, default=0)
    requested_date = Column(Date)
    scheduled_date = Column(Date)
    completed_date = Column(Date)
    notes = Column(String)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class ServiceOrderLine(Base):
    __tablename__ = 'service_order_lines'
    id = Column(Integer, primary_key=True)
    service_order_id = Column(Integer, nullable=False)
    part_id = Column(Integer, nullable=False)
    quantity_required = Column(Integer, nullable=False, info={'check': "quantity_required > 0"})
    quantity_reserved = Column(Integer, nullable=False, default=0)
    quantity_used = Column(Integer, nullable=False, default=0)
    line_status = Column(String, nullable=False, default='pending',
                         info={'check': "line_status IN ('pending','reserved','fulfilled','cancelled')"})
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class StockReservation(Base):
    __tablename__ = 'stock_reservations'
    id = Column(Integer, primary_key=True)
    part_id = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False, info={'check': "quantity > 0"})
    reservation_type = Column(String, nullable=False,
                              info={'check': "reservation_type IN ('purchase_order','service_order','manual')"})
    reference_id = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default='active',
                    info={'check': "status IN ('active','released','fulfilled','cancelled')"})
    notes = Column(String)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())

class InventoryTransaction(Base):
    __tablename__ = 'inventory_transactions'
    id = Column(Integer, primary_key=True)
    part_id = Column(Integer, nullable=False)
    transaction_type = Column(String, nullable=False,
                              info={'check': "transaction_type IN ('receipt','issue','adjustment','reservation','release','transfer')"})
    quantity = Column(Integer, nullable=False)
    reference_type = Column(String)
    reference_id = Column(Integer)
    notes = Column(String)
    performed_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    old_values = Column(String)
    new_values = Column(String)
    notes = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())

# Create engine and session
from sqlalchemy import create_engine
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create FastAPI app
app = FastAPI()

# Pydantic models for responses
class ServiceOrderResponse(BaseModel):
    id: int
    so_number: str

class CancelPurchaseOrderResponse(BaseModel):
    message: str
    purchase_order_id: int
    service_order_id: int
    reassigned_quantity: int

# Helper functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# API Endpoints
@app.get("/service_orders", response_model=ServiceOrderResponse, operation_id="find_service_order_by_number")
def find_service_order_by_number(so_number: str, db: Session = Depends(get_db)):
    service_order = db.query(ServiceOrder).filter(ServiceOrder.so_number == so_number).first()
    if not service_order:
        raise HTTPException(status_code=404, detail="Service order not found")
    return ServiceOrderResponse(id=service_order.id, so_number=service_order.so_number)

@app.put("/purchase_orders/{po_id}/cancel", response_model=CancelPurchaseOrderResponse, operation_id="cancel_purchase_order_and_reassign_stock")
def cancel_purchase_order_and_reassign_stock(
    po_id: int,
    so_number: str,
    db: Session = Depends(get_db)
):
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find purchase order
    purchase_order = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not purchase_order:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    
    # Check if purchase order is in submitted status
    if purchase_order.status != "submitted":
        raise HTTPException(status_code=400, detail="Purchase order must be in submitted status to cancel")
    
    # Find service order
    service_order = db.query(ServiceOrder).filter(ServiceOrder.so_number == so_number).first()
    if not service_order:
        raise HTTPException(status_code=404, detail="Service order not found")
    
    # Check if supplier lead time exceeds 45 days
    supplier = db.query(Supplier).filter(Supplier.id == purchase_order.supplier_id).first()
    if not supplier or supplier.lead_time_days <= 45:
        raise HTTPException(status_code=400, detail="Supplier lead time must exceed 45 days to cancel purchase order")
    
    # Find purchase order line
    po_line = db.query(PurchaseOrderLine).filter(PurchaseOrderLine.purchase_order_id == po_id).first()
    if not po_line:
        raise HTTPException(status_code=404, detail="Purchase order line not found")
    
    # Find part
    part = db.query(Part).filter(Part.id == po_line.part_id).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    
    # Check if there's a stock reservation for this purchase order
    stock_reservation = db.query(StockReservation).filter(
        StockReservation.reservation_type == "purchase_order",
        StockReservation.reference_id == po_id
    ).first()
    
    if not stock_reservation:
        raise HTTPException(status_code=400, detail="No stock reservation found for this purchase order")
    
    # Cancel the purchase order
    purchase_order.status = "cancelled"
    db.commit()
    
    # Release the stock reservation
    stock_reservation.status = "released"
    db.commit()
    
    # Update part reserved quantity
    part.reserved_quantity -= stock_reservation.quantity
    db.commit()
    
    # Create inventory transaction for release
    release_transaction = InventoryTransaction(
        part_id=part.id,
        transaction_type="release",
        quantity=stock_reservation.quantity,
        reference_type="purchase_order",
        reference_id=po_id,
        notes=f"Released {stock_reservation.quantity} units from PO-{purchase_order.po_number}",
        performed_by=current_user_id
    )
    db.add(release_transaction)
    db.commit()
    
    # Find or create service order line for this part
    service_order_line = db.query(ServiceOrderLine).filter(
        ServiceOrderLine.service_order_id == service_order.id,
        ServiceOrderLine.part_id == part.id
    ).first()
    
    if not service_order_line:
        # Create new service order line
        service_order_line = ServiceOrderLine(
            service_order_id=service_order.id,
            part_id=part.id,
            quantity_required=stock_reservation.quantity,
            quantity_reserved=stock_reservation.quantity,
            line_status="reserved"
        )
        db.add(service_order_line)
    else:
        # Update existing service order line
        service_order_line.quantity_reserved += stock_reservation.quantity
        service_order_line.line_status = "reserved"
    
    db.commit()
    
    # Update part reserved quantity for service order
    part.reserved_quantity += stock_reservation.quantity
    db.commit()
    
    # Create inventory transaction for reservation
    reservation_transaction = InventoryTransaction(
        part_id=part.id,
        transaction_type="reservation",
        quantity=stock_reservation.quantity,
        reference_type="service_order",
        reference_id=service_order.id,
        notes=f"Reserved {stock_reservation.quantity} units for SO-{service_order.so_number}",
        performed_by=current_user_id
    )
    db.add(reservation_transaction)
    db.commit()
    
    # Update stock reservation to point to service order
    stock_reservation.reservation_type = "service_order"
    stock_reservation.reference_id = service_order.id
    db.commit()
    
    # Add audit log
    audit_log = AuditLog(
        user_id=current_user_id,
        action="cancel",
        entity_type="purchase_order",
        entity_id=purchase_order.id,
        notes=f"Cancelled PO-{purchase_order.po_number} due to supplier lead time exceeding 45 days",
        new_values=f"{{\"status\": \"cancelled\"}}"
    )
    db.add(audit_log)
    db.commit()
    
    return CancelPurchaseOrderResponse(
        message="Purchase order cancelled and stock reassigned to service order",
        purchase_order_id=purchase_order.id,
        service_order_id=service_order.id,
        reassigned_quantity=stock_reservation.quantity
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
