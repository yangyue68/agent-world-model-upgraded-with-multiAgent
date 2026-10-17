import os
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Assume DATABASE_PATH is provided in the environment or context as per instructions
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# --- Pydantic Models ---

class LeaseResponse(BaseModel):
    id: int
    unit_id: int
    tenant_id: int
    start_date: str
    end_date: str
    monthly_rent: float
    pet_policy: str
    pet_deposit: float
    status: str

class AmendLeaseRequest(BaseModel):
    new_end_date: str
    new_monthly_rent: float
    new_pet_policy: str
    new_pet_deposit: float
    notes: Optional[str] = None

class AmendLeaseResponse(BaseModel):
    lease_id: int
    amendment_id: int


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def parse_date(date_str: str) -> date:
    """Parse ISO-8601 date string, handling both 'T...Z' and space-separated forms if necessary, 
    though for dates we usually just expect YYYY-MM-DD."""
    # Simple parsing for YYYY-MM-DD
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str}")

# --- Endpoints ---

@app.get(
    "/properties/{property_id}/units/{unit_number}/tenants/{tenant_id}/lease",
    response_model=LeaseResponse,
    operation_id="get_active_lease"
)
def get_active_lease(
    property_id: int = Path(..., description="Internal ID of the property"),
    unit_number: str = Path(..., description="Unit number within the property"),
    tenant_id: int = Path(..., description="Internal ID of the tenant"),
    db=Depends(get_db)
):
    """
    Retrieve the active lease record for a given tenant at a specific unit within a property.
    """
    query = text("""
        SELECT l.id, l.unit_id, l.tenant_id, l.start_date, l.end_date, 
               l.monthly_rent, l.pet_policy, l.pet_deposit, l.status
        FROM leases l
        JOIN units u ON l.unit_id = u.id
        WHERE u.property_id = :property_id
          AND u.unit_number = :unit_number
          AND l.tenant_id = :tenant_id
          AND l.status = 'active'
    """)
    
    result = db.execute(query, {
        "property_id": property_id,
        "unit_number": unit_number,
        "tenant_id": tenant_id
    }).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Active lease not found for the specified tenant, unit, and property.")

    return LeaseResponse(
        id=result[0],
        unit_id=result[1],
        tenant_id=result[2],
        start_date=str(result[3]),
        end_date=str(result[4]),
        monthly_rent=float(result[5]),
        pet_policy=result[6],
        pet_deposit=float(result[7]),
        status=result[8]
    )

@app.put(
    "/leases/{lease_id}/amend",
    response_model=AmendLeaseResponse,
    operation_id="amend_lease"
)
def amend_lease(
    lease_id: int = Path(..., description="Internal ID of the lease to amend"),
    body: AmendLeaseRequest = Body(...),
    db=Depends(get_db)
):
    """
    Update the lease end date, monthly rent, and pet policy for the specified lease.
    Records the amendment in the lease_amendments table.
    """
    # Get current user ID from environment variable
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context.")
    
    try:
        amended_by_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format.")

    # Fetch existing lease details to record old values
    lease_query = text("""
        SELECT id, unit_id, tenant_id, start_date, end_date, 
               monthly_rent, pet_policy, pet_deposit, status
        FROM leases
        WHERE id = :lease_id
    """)
    
    lease_result = db.execute(lease_query, {"lease_id": lease_id}).fetchone()

    if not lease_result:
        raise HTTPException(status_code=404, detail="Lease not found.")

    # Check if lease is active? The spec doesn't explicitly forbid amending non-active leases, 
    # but typically amendments apply to active ones. We'll proceed with update regardless unless business logic dictates otherwise.
    # However, the task implies updating an *active* lease. Let's assume standard behavior allows amendment.
    
    old_end_date = str(lease_result[4])
    old_monthly_rent = float(lease_result[5])
    old_pet_policy = lease_result[6]
    old_pet_deposit = float(lease_result[7])

    # Parse new end date
    try:
        new_end_date_obj = parse_date(body.new_end_date)
    except HTTPException:
        raise
    
    # Prepare parameters for update
    params = {
        "lease_id": lease_id,
        "new_end_date": body.new_end_date,
        "new_monthly_rent": body.new_monthly_rent,
        "new_pet_policy": body.new_pet_policy,
        "new_pet_deposit": body.new_pet_deposit,
        "updated_at": datetime.utcnow().isoformat(sep=' ', timespec='seconds')
    }

    # Update the lease record
    update_query = text("""
        UPDATE leases
        SET end_date = :new_end_date,
            monthly_rent = :new_monthly_rent,
            pet_policy = :new_pet_policy,
            pet_deposit = :new_pet_deposit,
            updated_at = :updated_at
        WHERE id = :lease_id
    """)
    
    db.execute(update_query, params)

    # Insert into lease_amendments
    insert_amendment_query = text("""
        INSERT INTO lease_amendments (
            lease_id,
            amended_by_user_id,
            amendment_date,
            old_end_date,
            new_end_date,
            old_monthly_rent,
            new_monthly_rent,
            old_pet_policy,
            new_pet_policy,
            old_pet_deposit,
            new_pet_deposit,
            notes,
            created_at,
            updated_at
        ) VALUES (
            :lease_id,
            :amended_by_user_id,
            :amendment_date,
            :old_end_date,
            :new_end_date,
            :old_monthly_rent,
            :new_monthly_rent,
            :old_pet_policy,
            :new_pet_policy,
            :old_pet_deposit,
            :new_pet_deposit,
            :notes,
            :created_at,
            :updated_at
        )
    """)
    
    now_iso = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
    
    amendment_params = {
        "lease_id": lease_id,
        "amended_by_user_id": amended_by_user_id,
        "amendment_date": now_iso,
        "old_end_date": old_end_date,
        "new_end_date": body.new_end_date,
        "old_monthly_rent": old_monthly_rent,
        "new_monthly_rent": body.new_monthly_rent,
        "old_pet_policy": old_pet_policy,
        "new_pet_policy": body.new_pet_policy,
        "old_pet_deposit": old_pet_deposit,
        "new_pet_deposit": body.new_pet_deposit,
        "notes": body.notes,
        "created_at": now_iso,
        "updated_at": now_iso
    }

    result = db.execute(insert_amendment_query, amendment_params)
    db.commit()

    # Get the last inserted row ID for the amendment
    # SQLite supports lastrowid via cursor, but SQLAlchemy Core execute returns a ResultProxy
    # We can fetch it using a separate query or rely on the fact that we know the structure.
    # A safer way in SQLAlchemy Core for SQLite is often to use `result.lastrowid` if available, 
    # but it depends on the dialect. Alternatively, query max(id).
    
    # Using a simple query to get the latest amendment ID for this lease
    get_last_id_query = text("""
        SELECT MAX(id) FROM lease_amendments WHERE lease_id = :lease_id
    """)
    amendment_id_result = db.execute(get_last_id_query, {"lease_id": lease_id}).scalar()
    
    if amendment_id_result is None:
         # Fallback if something went wrong, though commit succeeded
         raise HTTPException(status_code=500, detail="Failed to retrieve amendment ID.")

    return AmendLeaseResponse(
        lease_id=lease_id,
        amendment_id=int(amendment_id_result)
    )
