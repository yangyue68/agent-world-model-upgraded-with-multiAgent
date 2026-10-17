import os
from datetime import date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, REAL, ForeignKey, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    contact_name = Column(String)
    phone = Column(String)
    email = Column(String)
    address = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Part(Base):
    __tablename__ = "parts"
    id = Column(Integer, primary_key=True, index=True)
    part_number = Column(String, nullable=False, unique=True)
    description = Column(String)
    unit_price = Column(REAL)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, unique=True)
    full_name = Column(String)
    email = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    expected_delivery_date = Column(Date)
    status = Column(String, nullable=False, default="pending")
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    items = relationship("PurchaseOrderItem", back_populates="order")

class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id = Column(Integer, primary_key=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"))
    part_id = Column(Integer, ForeignKey("parts.id"))
    quantity = Column(Integer, nullable=False)
    unit_price = Column(REAL)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    order = relationship("PurchaseOrder", back_populates="items")
    part = relationship("Part")

# Request and Response Models
class SupplierResponse(BaseModel):
    id: int
    name: str

class PurchaseOrderItemRequest(BaseModel):
    part_number: str
    quantity: int

class CreatePurchaseOrderRequest(BaseModel):
    supplier_id: int
    items: List[PurchaseOrderItemRequest]
    expected_delivery_date: date

class PurchaseOrderResponse(BaseModel):
    id: int
    supplier_id: int
    expected_delivery_date: date
    status: str
    created_by: int
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

@app.get("/suppliers", response_model=List[SupplierResponse], operation_id="find_suppliers")
def find_suppliers(name: str, db = Depends(get_db)):
    suppliers = db.query(Supplier).filter(Supplier.name.contains(name)).all()
    return suppliers

@app.post("/purchase_orders", response_model=PurchaseOrderResponse, operation_id="create_purchase_order")
def create_purchase_order(order: CreatePurchaseOrderRequest, db = Depends(get_db)):
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Validate supplier exists
    supplier = db.query(Supplier).filter(Supplier.id == order.supplier_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    
    # Create purchase order
    db_order = PurchaseOrder(
        supplier_id=order.supplier_id,
        expected_delivery_date=order.expected_delivery_date,
        status="pending",
        created_by=current_user_id
    )
    db.add(db_order)
    db.commit()
    db.refresh(db_order)
    
    # Add items to purchase order
    for item in order.items:
        # Find part by part_number
        part = db.query(Part).filter(Part.part_number == item.part_number).first()
        if not part:
            raise HTTPException(status_code=404, detail=f"Part {item.part_number} not found")
        
        # Create purchase order item
        db_item = PurchaseOrderItem(
            purchase_order_id=db_order.id,
            part_id=part.id,
            quantity=item.quantity,
            unit_price=part.unit_price
        )
        db.add(db_item)
    
    db.commit()
    
    # Refresh order to get updated data
    db.refresh(db_order)
    
    # Convert to response model
    return PurchaseOrderResponse(
        id=db_order.id,
        supplier_id=db_order.supplier_id,
        expected_delivery_date=db_order.expected_delivery_date,
        status=db_order.status,
        created_by=db_order.created_by,
        created_at=db_order.created_at.isoformat() if db_order.created_at else None,
        updated_at=db_order.updated_at.isoformat() if db_order.updated_at else None
    )

if __name__ == "__main__":
    pass
