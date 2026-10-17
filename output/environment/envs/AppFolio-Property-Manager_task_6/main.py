import os
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class VendorCreate(BaseModel):
    name: str
    contact_name: str
    email: str
    phone: str
    service_category: str


class VendorResponse(BaseModel):
    id: int
    name: str
    contact_name: str
    email: str
    phone: str
    service_category: str


class LeadCreate(BaseModel):
    name: str
    email: str
    phone: str
    desired_unit_type: str
    desired_property_id: int
    desired_move_in_date: str


class LeadResponse(BaseModel):
    id: int
    name: str
    email: str
    phone: str
    desired_unit_type: str
    desired_property_id: int
    desired_move_in_date: str


class WorkOrderCreate(BaseModel):
    unit_id: int
    vendor_id: int
    description: str
    priority: str


class WorkOrderResponse(BaseModel):
    id: int
    unit_id: int
    vendor_id: int
    description: str
    priority: str
    status: str


@app.post("/vendors", response_model=VendorResponse, operation_id="create_hvac_vendor")
def create_hvac_vendor(vendor: VendorCreate):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                INSERT INTO vendors (name, contact_name, email, phone, service_category, created_at, updated_at)
                VALUES (:name, :contact_name, :email, :phone, :service_category, :created_at, :updated_at)
            """),
            {
                "name": vendor.name,
                "contact_name": vendor.contact_name,
                "email": vendor.email,
                "phone": vendor.phone,
                "service_category": vendor.service_category,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            },
        )
        vendor_id = result.lastrowid
        row = conn.execute(
            text("SELECT id, name, contact_name, email, phone, service_category FROM vendors WHERE id = :id"),
            {"id": vendor_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created vendor")
        return VendorResponse(
            id=row[0],
            name=row[1],
            contact_name=row[2],
            email=row[3],
            phone=row[4],
            service_category=row[5],
        )


@app.post("/leads", response_model=LeadResponse, operation_id="create_lead")
def create_lead(lead: LeadCreate):
    # Validate property exists
    with engine.connect() as conn:
        prop_row = conn.execute(
            text("SELECT id FROM properties WHERE id = :pid"),
            {"pid": lead.desired_property_id},
        ).fetchone()
        if not prop_row:
            raise HTTPException(status_code=400, detail="Property not found")

        result = conn.execute(
            text("""
                INSERT INTO leads (name, email, phone, desired_unit_type, desired_property_id, desired_move_in_date, status, created_at, updated_at)
                VALUES (:name, :email, :phone, :desired_unit_type, :desired_property_id, :desired_move_in_date, :status, :created_at, :updated_at)
            """),
            {
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "desired_unit_type": lead.desired_unit_type,
                "desired_property_id": lead.desired_property_id,
                "desired_move_in_date": lead.desired_move_in_date,
                "status": "new",
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            },
        )
        lead_id = result.lastrowid
        row = conn.execute(
            text("SELECT id, name, email, phone, desired_unit_type, desired_property_id, desired_move_in_date FROM leads WHERE id = :id"),
            {"id": lead_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created lead")
        return LeadResponse(
            id=row[0],
            name=row[1],
            email=row[2],
            phone=row[3],
            desired_unit_type=row[4],
            desired_property_id=row[5],
            desired_move_in_date=str(row[6]),
        )


@app.post("/work_orders", response_model=WorkOrderResponse, operation_id="create_work_order")
def create_work_order(work_order: WorkOrderCreate):
    # Validate unit exists
    with engine.connect() as conn:
        unit_row = conn.execute(
            text("SELECT id FROM units WHERE id = :uid"),
            {"uid": work_order.unit_id},
        ).fetchone()
        if not unit_row:
            raise HTTPException(status_code=400, detail="Unit not found")

        # Validate vendor exists
        vendor_row = conn.execute(
            text("SELECT id FROM vendors WHERE id = :vid"),
            {"vid": work_order.vendor_id},
        ).fetchone()
        if not vendor_row:
            raise HTTPException(status_code=400, detail="Vendor not found")

        # Get current user ID from environment
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        current_user_id = int(current_user_id_str) if current_user_id_str else None

        result = conn.execute(
            text("""
                INSERT INTO work_orders (unit_id, vendor_id, description, priority, status, created_by, created_at, updated_at)
                VALUES (:unit_id, :vendor_id, :description, :priority, :status, :created_by, :created_at, :updated_at)
            """),
            {
                "unit_id": work_order.unit_id,
                "vendor_id": work_order.vendor_id,
                "description": work_order.description,
                "priority": work_order.priority,
                "status": "open",
                "created_by": current_user_id,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            },
        )
        wo_id = result.lastrowid
        row = conn.execute(
            text("SELECT id, unit_id, vendor_id, description, priority, status FROM work_orders WHERE id = :id"),
            {"id": wo_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created work order")
        return WorkOrderResponse(
            id=row[0],
            unit_id=row[1],
            vendor_id=row[2],
            description=row[3],
            priority=row[4],
            status=row[5],
        )
