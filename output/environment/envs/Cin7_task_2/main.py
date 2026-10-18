import os
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    create_engine,
    select,
)
from sqlalchemy.orm import declarative_base, Session, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
Base = declarative_base()


# --- SQLAlchemy Models ---

class Suppliers(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    contact_name = Column(String)
    email = Column(String)
    phone = Column(String)
    address = Column(String)
    created_at = Column(DateTime, default=None)
    updated_at = Column(DateTime, default=None)


class Warehouses(Base):
    __tablename__ = "warehouses"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    address = Column(String)
    created_at = Column(DateTime, default=None)
    updated_at = Column(DateTime, default=None)


class Products(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String)
    preferred_supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    created_at = Column(DateTime, default=None)
    updated_at = Column(DateTime, default=None)
    
    supplier = relationship("Suppliers", foreign_keys=[preferred_supplier_id])


class Variants(Base):
    __tablename__ = "variants"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    sku = Column(String, unique=True, nullable=False)
    barcode = Column(String)
    price = Column(Float)
    cost = Column(Float)
    created_at = Column(DateTime, default=None)
    updated_at = Column(DateTime, default=None)
    
    product = relationship("Products", foreign_keys=[product_id])


class StockRecords(Base):
    __tablename__ = "stock_records"
    id = Column(Integer, primary_key=True)
    variant_id = Column(Integer, ForeignKey("variants.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    bin_location = Column(String)
    on_hand_quantity = Column(Integer, default=0)
    allocated_quantity = Column(Integer, default=0)
    available_quantity = Column(Integer, default=0)
    reorder_point = Column(Integer, default=0)
    created_at = Column(DateTime, default=None)
    updated_at = Column(DateTime, default=None)
    
    variant = relationship("Variants", foreign_keys=[variant_id])
    warehouse = relationship("Warehouses", foreign_keys=[warehouse_id])


# --- Pydantic Response Model ---

class StockRecordResponse(BaseModel):
    product_name: str
    sku: str
    bin_location: str
    on_hand_quantity: int
    allocated_quantity: int
    available_quantity: int
    reorder_point: int
    supplier_name: str

    class Config:
        from_attributes = True


# --- FastAPI App ---

app = FastAPI(title="Cin7 Task 2 API")


@app.get(
    "/stock_records/sydney_low_inventory",
    operation_id="find_sydney_low_inventory_stock",
    response_model=List[StockRecordResponse],
    summary="Find low inventory stock records in Sydney warehouse",
    description="Retrieve stock records from the Sydney warehouse where the variant SKU starts with 'AF-' and available quantity is less than or equal to the reorder point. Returns product name, SKU, bin location, on-hand quantity, allocated quantity, available quantity, reorder point, and preferred supplier name.",
    tags=["PrimaryResource"]
)
def find_sydney_low_inventory_stock():
    """
    Find all stock records in the 'Sydney' warehouse where the variant SKU starts with 'AF-' 
    and available quantity is less than or equal to its reorder point.
    """
    with Session(engine) as session:
        # Subquery to get Sydney warehouse ID
        sydney_warehouse_subq = select(Warehouses.id).where(Warehouses.name == "Sydney").scalar_subquery()
        
        # Main query
        stmt = (
            select(
                Products.name.label("product_name"),
                Variants.sku,
                StockRecords.bin_location,
                StockRecords.on_hand_quantity,
                StockRecords.allocated_quantity,
                StockRecords.available_quantity,
                StockRecords.reorder_point,
                Suppliers.name.label("supplier_name")
            )
            .join(Variants, StockRecords.variant_id == Variants.id)
            .join(Products, Variants.product_id == Products.id)
            .outerjoin(Suppliers, Products.preferred_supplier_id == Suppliers.id)
            .where(
                StockRecords.warehouse_id == sydney_warehouse_subq,
                Variants.sku.like("AF-%"),
                StockRecords.available_quantity <= StockRecords.reorder_point
            )
        )
        
        results = session.execute(stmt).fetchall()
        
        if not results:
            return []
            
        response_data = []
        for row in results:
            response_data.append(StockRecordResponse(
                product_name=row.product_name,
                sku=row.sku,
                bin_location=row.bin_location,
                on_hand_quantity=row.on_hand_quantity,
                allocated_quantity=row.allocated_quantity,
                available_quantity=row.available_quantity,
                reorder_point=row.reorder_point,
                supplier_name=row.supplier_name if row.supplier_name else ""
            ))
            
        return response_data
