import os
from datetime import datetime, date
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="AppFolio Property Manager API")


# Pydantic Models for Requests and Responses

class FloorPlanCreate(BaseModel):
    name: str
    square_feet: int
    market_rent: float

class TenantCreate(BaseModel):
    full_name: str
    email: str
    phone: str

class LeaseCreate(BaseModel):
    unit_id: int
    tenant_id: int
    start_date: date
    end_date: date
    monthly_rent: float
    security_deposit: float

class PropertyResponse(BaseModel):
    id: int
    name: str

class FloorPlanResponse(BaseModel):
    id: int
    property_id: int
    name: str
    square_feet: int
    market_rent: float

class TenantResponse(BaseModel):
    id: int
    full_name: str
    email: str
    phone: str

class LeaseResponse(BaseModel):
    id: int
    unit_id: int
    tenant_id: int
    start_date: date
    end_date: date
    monthly_rent: float
    security_deposit: float


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/properties", response_model=List[PropertyResponse], operation_id="find_properties")
def find_properties(name: str = Query(..., description="Property name")):
    """Find properties by name."""
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, name FROM properties WHERE name LIKE :name"),
            {"name": f"%{name}%"}
        )
        rows = result.fetchall()
        return [PropertyResponse(id=row.id, name=row.name) for row in rows]


@app.post("/properties/{property_id}/floor_plans", response_model=FloorPlanResponse, operation_id="create_floor_plan")
def create_floor_plan(property_id: int, floor_plan: FloorPlanCreate):
    """Create a new floor plan for a property."""
    # Verify property exists
    with engine.connect() as conn:
        prop_result = conn.execute(
            text("SELECT id FROM properties WHERE id = :pid"),
            {"pid": property_id}
        )
        if not prop_result.fetchone():
            raise HTTPException(status_code=404, detail="Property not found")

        # Check for duplicate name within the same property (UNIQUE constraint)
        dup_check = conn.execute(
            text("SELECT id FROM floor_plans WHERE property_id = :pid AND name = :name"),
            {"pid": property_id, "name": floor_plan.name}
        )
        if dup_check.fetchone():
            raise HTTPException(status_code=409, detail="Floor plan name already exists for this property")

        # Insert new floor plan
        insert_result = conn.execute(
            text("""
                INSERT INTO floor_plans (property_id, name, square_feet, market_rent, created_at, updated_at)
                VALUES (:property_id, :name, :square_feet, :market_rent, :created_at, :updated_at)
            """),
            {
                "property_id": property_id,
                "name": floor_plan.name,
                "square_feet": floor_plan.square_feet,
                "market_rent": floor_plan.market_rent,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
        )
        conn.commit()
        
        new_id = insert_result.lastrowid
        
        # Fetch to return consistent data
        fetch_result = conn.execute(
            text("SELECT id, property_id, name, square_feet, market_rent FROM floor_plans WHERE id = :id"),
            {"id": new_id}
        )
        row = fetch_result.fetchone()
        
        return FloorPlanResponse(
            id=row.id,
            property_id=row.property_id,
            name=row.name,
            square_feet=row.square_feet,
            market_rent=row.market_rent
        )


@app.post("/tenants", response_model=TenantResponse, operation_id="create_tenant")
def create_tenant(tenant: TenantCreate):
    """Create a new tenant record."""
    with engine.connect() as conn:
        # Check for duplicate email or phone? Schema doesn't enforce unique on these, 
        # but typically we might want to. The spec doesn't say so, just creates.
        # We will just insert.
        
        insert_result = conn.execute(
            text("""
                INSERT INTO tenants (full_name, email, phone, created_at, updated_at)
                VALUES (:full_name, :email, :phone, :created_at, :updated_at)
            """),
            {
                "full_name": tenant.full_name,
                "email": tenant.email,
                "phone": tenant.phone,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
        )
        conn.commit()
        
        new_id = insert_result.lastrowid
        
        fetch_result = conn.execute(
            text("SELECT id, full_name, email, phone FROM tenants WHERE id = :id"),
            {"id": new_id}
        )
        row = fetch_result.fetchone()
        
        return TenantResponse(
            id=row.id,
            full_name=row.full_name,
            email=row.email,
            phone=row.phone
        )


@app.post("/leases", response_model=LeaseResponse, operation_id="create_lease")
def create_lease(lease: LeaseCreate):
    """Create a new lease agreement."""
    with engine.connect() as conn:
        # Validate Unit exists
        unit_result = conn.execute(
            text("SELECT id FROM units WHERE id = :uid"),
            {"uid": lease.unit_id}
        )
        if not unit_result.fetchone():
            raise HTTPException(status_code=404, detail="Unit not found")

        # Validate Tenant exists
        tenant_result = conn.execute(
            text("SELECT id FROM tenants WHERE id = :tid"),
            {"tid": lease.tenant_id}
        )
        if not tenant_result.fetchone():
            raise HTTPException(status_code=404, detail="Tenant not found")

        # Validate dates (CHECK constraint end_date > start_date)
        if lease.end_date <= lease.start_date:
            raise HTTPException(status_code=422, detail="End date must be after start date")

        # Insert lease
        insert_result = conn.execute(
            text("""
                INSERT INTO leases (unit_id, tenant_id, start_date, end_date, monthly_rent, security_deposit, created_at, updated_at)
                VALUES (:unit_id, :tenant_id, :start_date, :end_date, :monthly_rent, :security_deposit, :created_at, :updated_at)
            """),
            {
                "unit_id": lease.unit_id,
                "tenant_id": lease.tenant_id,
                "start_date": lease.start_date,
                "end_date": lease.end_date,
                "monthly_rent": lease.monthly_rent,
                "security_deposit": lease.security_deposit,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
        )
        conn.commit()
        
        new_id = insert_result.lastrowid
        
        fetch_result = conn.execute(
            text("""
                SELECT id, unit_id, tenant_id, start_date, end_date, monthly_rent, security_deposit 
                FROM leases WHERE id = :id
            """),
            {"id": new_id}
        )
        row = fetch_result.fetchone()
        
        return LeaseResponse(
            id=row.id,
            unit_id=row.unit_id,
            tenant_id=row.tenant_id,
            start_date=row.start_date,
            end_date=row.end_date,
            monthly_rent=row.monthly_rent,
            security_deposit=row.security_deposit
        )
