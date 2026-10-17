import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Union

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, func, case
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
    email = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Property(Base):
    __tablename__ = "properties"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Unit(Base):
    __tablename__ = "units"
    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    unit_number = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    property = relationship("Property")

class Vendor(Base):
    __tablename__ = "vendors"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    contact_name = Column(String)
    phone = Column(String)
    email = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class WorkOrder(Base):
    __tablename__ = "work_orders"
    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"))
    vendor_id = Column(Integer, ForeignKey("vendors.id"))
    priority = Column(String, nullable=False)
    description = Column(Text)
    status = Column(String, nullable=False)
    date_created = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    property = relationship("Property")
    unit = relationship("Unit")
    vendor = relationship("Vendor")

# Pydantic Models
class WorkOrderResponse(BaseModel):
    id: int
    property: str
    unit: str
    priority: str
    description: str
    date_created: datetime
    days_open: int

class AverageDaysOpenByPriority(BaseModel):
    Low: float
    Medium: float
    High: float
    Urgent: float

class SummaryResponse(BaseModel):
    total_count: int
    average_days_open_by_priority: AverageDaysOpenByPriority

class MaintenanceWorkOrdersResponse(BaseModel):
    work_orders: List[WorkOrderResponse]
    summary: SummaryResponse

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/maintenance-work-orders", response_model=MaintenanceWorkOrdersResponse, operation_id="find_maintenance_work_orders")
def find_maintenance_work_orders(
    vendor_name: str = Query(..., description="Name of the vendor to filter work orders by"),
    days_back: int = Query(45, description="Number of days back to filter work orders by creation date (default is 45)"),
    statuses: Optional[List[str]] = Query(None, description="List of statuses to filter work orders by (Open, In Progress, On Hold)")
):
    db = next(get_db())
    
    # Get current user from environment
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    
    # Define the base query
    query = db.query(WorkOrder, Property.name.label("property_name"), Unit.unit_number.label("unit_number"), Vendor.name.label("vendor_name"))
    
    # Join with related tables
    query = query.join(Property, WorkOrder.property_id == Property.id)\
                 .join(Unit, WorkOrder.unit_id == Unit.id, isouter=True)\
                 .join(Vendor, WorkOrder.vendor_id == Vendor.id)
    
    # Filter by vendor name
    query = query.filter(Vendor.name == vendor_name)
    
    # Filter by date range
    cutoff_date = datetime.utcnow() - timedelta(days=days_back)
    query = query.filter(WorkOrder.date_created >= cutoff_date)
    
    # Filter by status if provided
    if statuses:
        query = query.filter(WorkOrder.status.in_(statuses))
    else:
        # Default statuses: Open, In Progress, On Hold
        query = query.filter(WorkOrder.status.in_(["Open", "In Progress", "On Hold"]))
    
    # Get work orders
    work_orders_data = query.all()
    
    # Prepare work orders response
    work_orders = []
    for wo, property_name, unit_number, vendor_name in work_orders_data:
        days_open = (datetime.utcnow() - wo.date_created).days
        work_order_response = WorkOrderResponse(
            id=wo.id,
            property=property_name,
            unit=unit_number if unit_number else "",
            priority=wo.priority,
            description=wo.description,
            date_created=wo.date_created,
            days_open=days_open
        )
        work_orders.append(work_order_response)
    
    # Calculate summary statistics
    total_count = len(work_orders)
    
    # Calculate average days open by priority
    avg_days_by_priority_query = db.query(
        WorkOrder.priority,
        func.avg(func.julianday(datetime.utcnow()) - func.julianday(WorkOrder.date_created)).label("avg_days")
    ).filter(
        WorkOrder.vendor_id == Vendor.id,
        Vendor.name == vendor_name,
        WorkOrder.date_created >= cutoff_date,
        WorkOrder.status.in_(statuses if statuses else ["Open", "In Progress", "On Hold"])
    ).group_by(WorkOrder.priority)
    
    avg_days_by_priority_result = avg_days_by_priority_query.all()
    
    # Create dictionary for average days by priority
    avg_days_by_priority_dict = {
        "Low": 0.0,
        "Medium": 0.0,
        "High": 0.0,
        "Urgent": 0.0
    }
    
    for priority, avg_days in avg_days_by_priority_result:
        avg_days_by_priority_dict[priority] = round(float(avg_days), 2)
    
    # Create summary response
    summary = SummaryResponse(
        total_count=total_count,
        average_days_open_by_priority=AverageDaysOpenByPriority(**avg_days_by_priority_dict)
    )
    
    return MaintenanceWorkOrdersResponse(work_orders=work_orders, summary=summary)
