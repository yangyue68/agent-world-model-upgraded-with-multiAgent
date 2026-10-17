import os
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, func, case
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
    address = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Part(Base):
    __tablename__ = "parts"
    
    id = Column(Integer, primary_key=True, index=True)
    part_number = Column(String, unique=True, nullable=False)
    description = Column(String)
    is_serialized = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("is_serialized IN (0, 1)"),)

class InventoryUnit(Base):
    __tablename__ = "inventory_units"
    
    id = Column(Integer, primary_key=True, index=True)
    part_id = Column(Integer, ForeignKey("parts.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    serial_number = Column(String, nullable=False)
    status = Column(String, nullable=False, default="available")
    allocated_to_order_id = Column(Integer)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("status IN ('available', 'allocated', 'reserved', 'shipped', 'damaged')"),)

class ServiceOrder(Base):
    __tablename__ = "service_orders"
    
    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String, unique=True, nullable=False)
    status = Column(String, nullable=False, default="open")
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("status IN ('open', 'in_progress', 'completed', 'cancelled')"),)

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# Response model
class AvailabilityResponse(BaseModel):
    part_number: str
    warehouse_code: str
    available_units_count: int
    serial_numbers: List[str]
    is_available: bool

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/inventory/availability", response_model=AvailabilityResponse, operation_id="check_part_availability")
def check_part_availability(
    part_number: str = Query(..., description="The part number to check availability for"),
    warehouse_code: str = Query(..., description="The warehouse code where the part should be checked"),
    minimum_available: Optional[int] = Query(2, description="Minimum number of available units required (default is 2)")
):
    db = next(get_db())
    
    # Get part and warehouse IDs
    part = db.query(Part).filter(Part.part_number == part_number).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    
    warehouse = db.query(Warehouse).filter(Warehouse.code == warehouse_code).first()
    if not warehouse:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    
    # Count available units
    available_units = (
        db.query(InventoryUnit)
        .filter(
            InventoryUnit.part_id == part.id,
            InventoryUnit.warehouse_id == warehouse.id,
            InventoryUnit.status == "available"
        )
        .all()
    )
    
    # Get serial numbers of available units
    serial_numbers = [unit.serial_number for unit in available_units]
    
    # Check if we have enough available units
    is_available = len(available_units) >= minimum_available
    
    return AvailabilityResponse(
        part_number=part_number,
        warehouse_code=warehouse_code,
        available_units_count=len(available_units),
        serial_numbers=serial_numbers,
        is_available=is_available
    )
