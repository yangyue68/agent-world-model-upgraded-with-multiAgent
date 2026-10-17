import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, REAL, ForeignKey, func, case, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Warehouse(Base):
    __tablename__ = "warehouses"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    address = Column(Text)
    city = Column(String)
    state = Column(String)
    postal_code = Column(String)
    country = Column(String)
    is_active = Column(Integer, nullable=False, default=1)

class Part(Base):
    __tablename__ = "parts"
    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    category = Column(String)
    brand = Column(String)
    unit_cost = Column(REAL)
    is_active = Column(Integer, nullable=False, default=1)

class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True, index=True)
    part_id = Column(Integer, ForeignKey("parts.id"))
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"))
    quantity_on_hand = Column(Integer, nullable=False, default=0)
    quantity_reserved = Column(Integer, nullable=False, default=0)
    quantity_committed = Column(Integer, nullable=False, default=0)
    reorder_point = Column(Integer, nullable=False, default=0)
    reorder_quantity = Column(Integer, nullable=False, default=0)
    bin_location = Column(String)
    last_counted_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# Response model
class WarehouseInfo(BaseModel):
    code: str
    name: str
    net_available: int

class StockComparisonResponse(BaseModel):
    part_sku: str
    warehouse_1: WarehouseInfo
    warehouse_2: WarehouseInfo
    difference: int
    higher_availability_warehouse: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/inventory/stock-comparison", response_model=StockComparisonResponse, operation_id="compare_stock_availability")
def compare_stock_availability(
    part_sku: str = Query(..., description="SKU of the aftermarket part to compare"),
    warehouse_code_1: str = Query(..., description="Code of the first warehouse (e.g., WH-DAL)"),
    warehouse_code_2: str = Query(..., description="Code of the second warehouse (e.g., WH-ATL)"),
):
    db = next(get_db())
    
    # Get part ID
    part = db.query(Part).filter(Part.sku == part_sku).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    
    # Get warehouse IDs
    warehouse_1 = db.query(Warehouse).filter(Warehouse.code == warehouse_code_1).first()
    warehouse_2 = db.query(Warehouse).filter(Warehouse.code == warehouse_code_2).first()
    
    if not warehouse_1:
        raise HTTPException(status_code=404, detail=f"Warehouse {warehouse_code_1} not found")
    if not warehouse_2:
        raise HTTPException(status_code=404, detail=f"Warehouse {warehouse_code_2} not found")
    
    # Calculate net available quantities
    net_available_1_subq = (
        db.query(
            func.sum(
                case((Inventory.quantity_on_hand > 0, Inventory.quantity_on_hand), else_=0) - 
                case((Inventory.quantity_reserved > 0, Inventory.quantity_reserved), else_=0) - 
                case((Inventory.quantity_committed > 0, Inventory.quantity_committed), else_=0)
            ).label('net_available')
        )
        .filter(
            and_(
                Inventory.part_id == part.id,
                Inventory.warehouse_id == warehouse_1.id
            )
        )
        .subquery()
    )
    
    net_available_2_subq = (
        db.query(
            func.sum(
                case((Inventory.quantity_on_hand > 0, Inventory.quantity_on_hand), else_=0) - 
                case((Inventory.quantity_reserved > 0, Inventory.quantity_reserved), else_=0) - 
                case((Inventory.quantity_committed > 0, Inventory.quantity_committed), else_=0)
            ).label('net_available')
        )
        .filter(
            and_(
                Inventory.part_id == part.id,
                Inventory.warehouse_id == warehouse_2.id
            )
        )
        .subquery()
    )
    
    # Execute queries
    net_available_1 = db.query(net_available_1_subq.c.net_available).scalar() or 0
    net_available_2 = db.query(net_available_2_subq.c.net_available).scalar() or 0
    
    # Determine difference and higher availability
    difference = net_available_1 - net_available_2
    higher_availability_warehouse = warehouse_code_1 if net_available_1 > net_available_2 else warehouse_code_2 if net_available_2 > net_available_1 else "tie"
    
    # Build response
    response = StockComparisonResponse(
        part_sku=part_sku,
        warehouse_1=WarehouseInfo(
            code=warehouse_code_1,
            name=warehouse_1.name,
            net_available=net_available_1
        ),
        warehouse_2=WarehouseInfo(
            code=warehouse_code_2,
            name=warehouse_2.name,
            net_available=net_available_2
        ),
        difference=difference,
        higher_availability_warehouse=higher_availability_warehouse
    )
    
    return response
