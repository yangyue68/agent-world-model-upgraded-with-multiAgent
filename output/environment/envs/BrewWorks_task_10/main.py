import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, Text, ForeignKey, CheckConstraint, func, case
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
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    sku = Column(String, unique=True, nullable=False)
    description = Column(Text)
    unit = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class InventoryLocation(Base):
    __tablename__ = "inventory_locations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    address = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class InventoryLot(Base):
    __tablename__ = "inventory_lots"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    lot_code = Column(String, nullable=False)
    quantity = Column(Integer, default=0)
    location_id = Column(Integer, ForeignKey("inventory_locations.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    contact_name = Column(String)
    email = Column(String)
    phone = Column(String)
    address = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class DistributionOrder(Base):
    __tablename__ = "distribution_orders"
    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String, unique=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    status = Column(String, default="pending")
    scheduled_delivery_date = Column(Date)
    actual_delivery_date = Column(Date)
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class DistributionOrderLine(Base):
    __tablename__ = "distribution_order_lines"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("distribution_orders.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    lot_id = Column(Integer, ForeignKey("inventory_lots.id"))
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"
    id = Column(Integer, primary_key=True, index=True)
    inventory_lot_id = Column(Integer, ForeignKey("inventory_lots.id"))
    transaction_type = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    reference_type = Column(String)
    reference_id = Column(Integer)
    notes = Column(Text)
    performed_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class ReleasedItem(BaseModel):
    product_id: int
    lot_code: str
    quantity: int

class CancelDistributionOrderResponse(BaseModel):
    order_id: int
    new_status: str
    released_items: List[ReleasedItem]

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/distribution_orders/{order_id}/cancel", response_model=CancelDistributionOrderResponse, operation_id="cancel_distribution_order")
def cancel_distribution_order(
    order_id: int = Path(..., title="Internal ID of the distribution order to cancel"),
    db = Depends(get_db)
):
    # Get the distribution order
    order = db.query(DistributionOrder).filter(DistributionOrder.id == order_id).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Distribution order not found")
    
    # Check if order is in a cancellable state
    if order.status not in ("scheduled", "on_hold"):
        raise HTTPException(status_code=400, detail="Order must be in 'scheduled' or 'on_hold' status to be cancelled")
    
    # Get all lines for this order
    order_lines = db.query(DistributionOrderLine).filter(DistributionOrderLine.order_id == order_id).all()
    
    released_items = []
    
    # Process each line to release reserved inventory
    for line in order_lines:
        if line.lot_id:
            # Find the inventory lot
            lot = db.query(InventoryLot).filter(InventoryLot.id == line.lot_id).first()
            
            if lot:
                # Create a release transaction
                transaction = InventoryTransaction(
                    inventory_lot_id=lot.id,
                    transaction_type="release",
                    quantity=line.quantity,
                    reference_type="distribution_order",
                    reference_id=order_id,
                    notes=f"Released from order {order.order_number}",
                    performed_by=int(os.environ.get("AWM_CURRENT_USER_ID", 0))
                )
                
                db.add(transaction)
                
                # Update lot quantity
                lot.quantity += line.quantity
                
                # Add to released items
                released_items.append(ReleasedItem(
                    product_id=line.product_id,
                    lot_code=lot.lot_code,
                    quantity=line.quantity
                ))
    
    # Update order status
    order.status = "cancelled"
    order.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(order)
    
    return CancelDistributionOrderResponse(
        order_id=order.id,
        new_status=order.status,
        released_items=released_items
    )
