import os
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, case
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

class Warehouse(Base):
    __tablename__ = "warehouses"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    address = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Bin(Base):
    __tablename__ = "bins"
    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    code = Column(String, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    warehouse = relationship("Warehouse")

class Part(Base):
    __tablename__ = "parts"
    id = Column(Integer, primary_key=True, index=True)
    oem_part_number = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String)
    manufacturer = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True, index=True)
    lot_number = Column(String, unique=True, nullable=False)
    part_id = Column(Integer, ForeignKey("parts.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    part = relationship("Part")

class Barcode(Base):
    __tablename__ = "barcodes"
    id = Column(Integer, primary_key=True, index=True)
    barcode = Column(String, unique=True, nullable=False)
    part_id = Column(Integer, ForeignKey("parts.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    part = relationship("Part")

class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True, index=True)
    part_id = Column(Integer, ForeignKey("parts.id"))
    lot_id = Column(Integer, ForeignKey("lots.id"))
    bin_id = Column(Integer, ForeignKey("bins.id"))
    quantity = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    part = relationship("Part")
    lot = relationship("Lot")
    bin = relationship("Bin")

class ReceivingOrder(Base):
    __tablename__ = "receiving_orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    bin_id = Column(Integer, ForeignKey("bins.id"))
    part_id = Column(Integer, ForeignKey("parts.id"))
    lot_id = Column(Integer, ForeignKey("lots.id"))
    quantity = Column(Integer, nullable=False)
    status = Column(String, default="pending", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User")
    warehouse = relationship("Warehouse")
    bin = relationship("Bin")
    part = relationship("Part")
    lot = relationship("Lot")

class ReceivingOrderItem(Base):
    __tablename__ = "receiving_order_items"
    id = Column(Integer, primary_key=True, index=True)
    receiving_order_id = Column(Integer, ForeignKey("receiving_orders.id"))
    barcode_id = Column(Integer, ForeignKey("barcodes.id"))
    quantity = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    receiving_order = relationship("ReceivingOrder")
    barcode = relationship("Barcode")

# Pydantic Models
class ReceivingOrderCreate(BaseModel):
    user_id: int
    warehouse_code: str
    bin_code: str
    oem_part_number: str
    lot_number: str
    quantity: int

class ReceivingOrderItemCreate(BaseModel):
    receiving_order_id: int
    barcode: str

class ReceivingOrderComplete(BaseModel):
    order_id: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/receiving-orders", response_model=dict, operation_id="create_receiving_order")
def create_receiving_order(order: ReceivingOrderCreate, db=None):
    if db is None:
        db = next(get_db())
    
    # Get user
    user = db.query(User).filter(User.id == order.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get warehouse
    warehouse = db.query(Warehouse).filter(Warehouse.code == order.warehouse_code).first()
    if not warehouse:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    
    # Get bin
    bin_obj = db.query(Bin).filter(Bin.warehouse_id == warehouse.id, Bin.code == order.bin_code).first()
    if not bin_obj:
        raise HTTPException(status_code=404, detail="Bin not found")
    
    # Get part
    part = db.query(Part).filter(Part.oem_part_number == order.oem_part_number).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    
    # Get lot
    lot = db.query(Lot).filter(Lot.lot_number == order.lot_number, Lot.part_id == part.id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    
    # Create receiving order
    receiving_order = ReceivingOrder(
        user_id=order.user_id,
        warehouse_id=warehouse.id,
        bin_id=bin_obj.id,
        part_id=part.id,
        lot_id=lot.id,
        quantity=order.quantity
    )
    db.add(receiving_order)
    db.commit()
    db.refresh(receiving_order)
    
    return {
        "id": receiving_order.id,
        "status": receiving_order.status,
        "created_at": receiving_order.created_at.isoformat()
    }

@app.post("/receiving-order-items", response_model=dict, operation_id="add_receiving_order_item")
def add_receiving_order_item(item: ReceivingOrderItemCreate, db=None):
    if db is None:
        db = next(get_db())
    
    # Get receiving order
    receiving_order = db.query(ReceivingOrder).filter(ReceivingOrder.id == item.receiving_order_id).first()
    if not receiving_order:
        raise HTTPException(status_code=404, detail="Receiving order not found")
    
    # Get barcode
    barcode = db.query(Barcode).filter(Barcode.barcode == item.barcode).first()
    if not barcode:
        raise HTTPException(status_code=404, detail="Barcode not found")
    
    # Create receiving order item
    receiving_order_item = ReceivingOrderItem(
        receiving_order_id=item.receiving_order_id,
        barcode_id=barcode.id
    )
    db.add(receiving_order_item)
    db.commit()
    db.refresh(receiving_order_item)
    
    return {
        "id": receiving_order_item.id,
        "quantity": receiving_order_item.quantity
    }

@app.put("/receiving-orders/{order_id}/complete", response_model=dict, operation_id="complete_receiving_order")
def complete_receiving_order(order_id: int, db=None):
    if db is None:
        db = next(get_db())
    
    # Get receiving order
    receiving_order = db.query(ReceivingOrder).filter(ReceivingOrder.id == order_id).first()
    if not receiving_order:
        raise HTTPException(status_code=404, detail="Receiving order not found")
    
    # Update status to completed
    receiving_order.status = "completed"
    db.commit()
    db.refresh(receiving_order)
    
    # Get all items for this order
    items = db.query(ReceivingOrderItem).filter(ReceivingOrderItem.receiving_order_id == order_id).all()
    
    # Add quantities to inventory
    for item in items:
        barcode = db.query(Barcode).filter(Barcode.id == item.barcode_id).first()
        if barcode:
            # Check if inventory entry exists
            inventory_entry = db.query(Inventory).filter(
                Inventory.part_id == receiving_order.part_id,
                Inventory.lot_id == receiving_order.lot_id,
                Inventory.bin_id == receiving_order.bin_id
            ).first()
            
            if inventory_entry:
                inventory_entry.quantity += item.quantity
            else:
                inventory_entry = Inventory(
                    part_id=receiving_order.part_id,
                    lot_id=receiving_order.lot_id,
                    bin_id=receiving_order.bin_id,
                    quantity=item.quantity
                )
                db.add(inventory_entry)
    
    db.commit()
    
    return {
        "id": receiving_order.id,
        "status": receiving_order.status
    }
