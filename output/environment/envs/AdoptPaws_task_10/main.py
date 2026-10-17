import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models ---

class VolunteerLookupResponse(BaseModel):
    id: int
    user_id: int
    full_name: str
    status: str


class ShelterLookupResponse(BaseModel):
    id: int
    name: str
    is_main: int


class VolunteerShiftResponse(BaseModel):
    id: int
    shift_code: str
    volunteer_id: int
    shelter_id: int
    shift_date: str
    start_time: str
    end_time: str
    status: str
    cancellation_reason: Optional[str] = None


class CancelShiftRequest(BaseModel):
    reason: str


class CancelShiftResponse(BaseModel):
    id: int
    shift_code: str
    status: str
    cancellation_reason: str
    cancelled_at: str


class DonorLookupResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    full_name: str
    email: Optional[str] = None


class DonationResponse(BaseModel):
    id: int
    donation_code: str
    donor_id: int
    amount: float
    donation_date: str
    designation: Optional[str] = None
    status: str
    void_reason: Optional[str] = None


class VoidDonationRequest(BaseModel):
    reason: str


class VoidDonationResponse(BaseModel):
    id: int
    donation_code: str
    status: str
    void_reason: str
    voided_at: str


class DonationAllocationResponse(BaseModel):
    id: int
    donation_id: int
    volunteer_shift_id: int
    allocated_amount: float
    status: str


class VoidAllocationResponse(BaseModel):
    id: int
    status: str
    voided_at: str


# --- Helper Functions ---

def get_current_user_id() -> int:
    """Read the simulated logged-in actor from environment."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="No authenticated user found")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


def execute_query(sql: str, params: dict = None) -> list:
    """Execute a SELECT query and return rows as dicts."""
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        columns = result.keys()
        rows = []
        for row in result.fetchall():
            rows.append(dict(zip(columns, row)))
        return rows


def execute_update(sql: str, params: dict = None) -> None:
    """Execute an UPDATE/INSERT/DELETE statement."""
    with engine.connect() as conn:
        conn.execute(text(sql), params or {})
        conn.commit()


# --- Endpoints ---

@app.get("/volunteers/lookup", response_model=List[VolunteerLookupResponse], operation_id="lookup_volunteers")
def lookup_volunteers(
    name: Optional[str] = Query(None, description="Partial or full name of the volunteer's user"),
    email: Optional[str] = Query(None, description="Exact email address of the volunteer's user")
):
    conditions = []
    params = {}
    
    if name:
        conditions.append("u.full_name LIKE :name")
        params["name"] = f"%{name}%"
    
    if email:
        conditions.append("u.email = :email")
        params["email"] = email
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
        SELECT v.id, v.user_id, u.full_name, v.status
        FROM volunteers v
        JOIN users u ON v.user_id = u.id
        WHERE {where_clause}
    """
    
    rows = execute_query(sql, params)
    return [VolunteerLookupResponse(**row) for row in rows]


@app.get("/shelters/lookup", response_model=List[ShelterLookupResponse], operation_id="lookup_shelters")
def lookup_shelters(
    name: Optional[str] = Query(None, description="Partial or full name of the shelter"),
    is_main: Optional[bool] = Query(None, description="Filter for main shelters only if true")
):
    conditions = []
    params = {}
    
    if name:
        conditions.append("s.name LIKE :name")
        params["name"] = f"%{name}%"
    
    if is_main is not None:
        conditions.append("s.is_main = :is_main")
        params["is_main"] = 1 if is_main else 0
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
        SELECT s.id, s.name, s.is_main
        FROM shelters s
        WHERE {where_clause}
    """
    
    rows = execute_query(sql, params)
    return [ShelterLookupResponse(**row) for row in rows]


@app.get("/volunteer-shifts/{shift_code}", response_model=VolunteerShiftResponse, operation_id="get_volunteer_shift_by_code")
def get_volunteer_shift_by_code(shift_code: str):
    sql = """
        SELECT id, shift_code, volunteer_id, shelter_id, shift_date, start_time, end_time, status, cancellation_reason
        FROM volunteer_shifts
        WHERE shift_code = :shift_code
    """
    
    rows = execute_query(sql, {"shift_code": shift_code})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Shift with code '{shift_code}' not found")
    
    row = rows[0]
    # Convert date/time objects to strings if needed
    shift_date = str(row["shift_date"]) if row["shift_date"] else None
    start_time = str(row["start_time"]) if row["start_time"] else None
    end_time = str(row["end_time"]) if row["end_time"] else None
    
    return VolunteerShiftResponse(
        id=row["id"],
        shift_code=row["shift_code"],
        volunteer_id=row["volunteer_id"],
        shelter_id=row["shelter_id"],
        shift_date=shift_date,
        start_time=start_time,
        end_time=end_time,
        status=row["status"],
        cancellation_reason=row["cancellation_reason"]
    )


@app.patch("/volunteer-shifts/{shift_code}/cancel", response_model=CancelShiftResponse, operation_id="cancel_volunteer_shift")
def cancel_volunteer_shift(shift_code: str, body: CancelShiftRequest):
    current_user_id = get_current_user_id()
    
    # First verify the shift exists and is scheduled
    check_sql = """
        SELECT id, status FROM volunteer_shifts WHERE shift_code = :shift_code
    """
    rows = execute_query(check_sql, {"shift_code": shift_code})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Shift with code '{shift_code}' not found")
    
    shift = rows[0]
    if shift["status"] != "scheduled":
        raise HTTPException(status_code=400, detail=f"Cannot cancel shift with status '{shift['status']}'. Only 'scheduled' shifts can be cancelled.")
    
    now = datetime.now(timezone.utc).isoformat()
    
    update_sql = """
        UPDATE volunteer_shifts
        SET status = 'cancelled',
            cancellation_reason = :reason,
            cancelled_by = :user_id,
            cancelled_at = :now,
            updated_at = :now
        WHERE shift_code = :shift_code
    """
    
    execute_update(update_sql, {
        "reason": body.reason,
        "user_id": current_user_id,
        "now": now,
        "shift_code": shift_code
    })
    
    # Fetch updated record
    fetch_sql = """
        SELECT id, shift_code, status, cancellation_reason, cancelled_at
        FROM volunteer_shifts
        WHERE shift_code = :shift_code
    """
    updated_rows = execute_query(fetch_sql, {"shift_code": shift_code})
    updated = updated_rows[0]
    
    cancelled_at_str = str(updated["cancelled_at"]) if updated["cancelled_at"] else now
    
    return CancelShiftResponse(
        id=updated["id"],
        shift_code=updated["shift_code"],
        status=updated["status"],
        cancellation_reason=updated["cancellation_reason"],
        cancelled_at=cancelled_at_str
    )


@app.get("/donors/lookup", response_model=List[DonorLookupResponse], operation_id="lookup_donors")
def lookup_donors(
    name: Optional[str] = Query(None, description="Partial or full name of the donor"),
    email: Optional[str] = Query(None, description="Exact email address of the donor")
):
    conditions = []
    params = {}
    
    if name:
        conditions.append("d.full_name LIKE :name")
        params["name"] = f"%{name}%"
    
    if email:
        conditions.append("d.email = :email")
        params["email"] = email
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
        SELECT d.id, d.user_id, d.full_name, d.email
        FROM donors d
        WHERE {where_clause}
    """
    
    rows = execute_query(sql, params)
    return [DonorLookupResponse(**row) for row in rows]


@app.get("/donations/{donation_code}", response_model=DonationResponse, operation_id="get_donation_by_code")
def get_donation_by_code(donation_code: str):
    sql = """
        SELECT id, donation_code, donor_id, amount, donation_date, designation, status, void_reason
        FROM donations
        WHERE donation_code = :donation_code
    """
    
    rows = execute_query(sql, {"donation_code": donation_code})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Donation with code '{donation_code}' not found")
    
    row = rows[0]
    donation_date = str(row["donation_date"]) if row["donation_date"] else None
    
    return DonationResponse(
        id=row["id"],
        donation_code=row["donation_code"],
        donor_id=row["donor_id"],
        amount=float(row["amount"]),
        donation_date=donation_date,
        designation=row["designation"],
        status=row["status"],
        void_reason=row["void_reason"]
    )


@app.patch("/donations/{donation_code}/void", response_model=VoidDonationResponse, operation_id="void_donation")
def void_donation(donation_code: str, body: VoidDonationRequest):
    current_user_id = get_current_user_id()
    
    # Verify donation exists and is active
    check_sql = """
        SELECT id, status FROM donations WHERE donation_code = :donation_code
    """
    rows = execute_query(check_sql, {"donation_code": donation_code})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Donation with code '{donation_code}' not found")
    
    donation = rows[0]
    if donation["status"] != "active":
        raise HTTPException(status_code=400, detail=f"Cannot void donation with status '{donation['status']}'. Only 'active' donations can be voided.")
    
    now = datetime.now(timezone.utc).isoformat()
    
    update_sql = """
        UPDATE donations
        SET status = 'voided',
            void_reason = :reason,
            voided_by = :user_id,
            voided_at = :now,
            updated_at = :now
        WHERE donation_code = :donation_code
    """
    
    execute_update(update_sql, {
        "reason": body.reason,
        "user_id": current_user_id,
        "now": now,
        "donation_code": donation_code
    })
    
    # Fetch updated record
    fetch_sql = """
        SELECT id, donation_code, status, void_reason, voided_at
        FROM donations
        WHERE donation_code = :donation_code
    """
    updated_rows = execute_query(fetch_sql, {"donation_code": donation_code})
    updated = updated_rows[0]
    
    voided_at_str = str(updated["voided_at"]) if updated["voided_at"] else now
    
    return VoidDonationResponse(
        id=updated["id"],
        donation_code=updated["donation_code"],
        status=updated["status"],
        void_reason=updated["void_reason"],
        voided_at=voided_at_str
    )


@app.get("/donation-allocations", response_model=List[DonationAllocationResponse], operation_id="find_donation_allocations")
def find_donation_allocations(
    donation_id: Optional[int] = Query(None, description="Filter by internal Donation ID"),
    volunteer_shift_id: Optional[int] = Query(None, description="Filter by internal Volunteer Shift ID")
):
    conditions = []
    params = {}
    
    if donation_id is not None:
        conditions.append("da.donation_id = :donation_id")
        params["donation_id"] = donation_id
    
    if volunteer_shift_id is not None:
        conditions.append("da.volunteer_shift_id = :volunteer_shift_id")
        params["volunteer_shift_id"] = volunteer_shift_id
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
        SELECT da.id, da.donation_id, da.volunteer_shift_id, da.allocated_amount, da.status
        FROM donation_allocations da
        WHERE {where_clause}
    """
    
    rows = execute_query(sql, params)
    return [DonationAllocationResponse(**row) for row in rows]


@app.patch("/donation-allocations/{allocation_id}/void", response_model=VoidAllocationResponse, operation_id="void_donation_allocation")
def void_donation_allocation(allocation_id: int):
    current_user_id = get_current_user_id()
    
    # Verify allocation exists and is active
    check_sql = """
        SELECT id, status FROM donation_allocations WHERE id = :allocation_id
    """
    rows = execute_query(check_sql, {"allocation_id": allocation_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Allocation with ID '{allocation_id}' not found")
    
    allocation = rows[0]
    if allocation["status"] != "active":
        raise HTTPException(status_code=400, detail=f"Cannot void allocation with status '{allocation['status']}'. Only 'active' allocations can be voided.")
    
    now = datetime.now(timezone.utc).isoformat()
    
    update_sql = """
        UPDATE donation_allocations
        SET status = 'voided',
            voided_by = :user_id,
            voided_at = :now,
            updated_at = :now
        WHERE id = :allocation_id
    """
    
    execute_update(update_sql, {
        "user_id": current_user_id,
        "now": now,
        "allocation_id": allocation_id
    })
    
    # Fetch updated record
    fetch_sql = """
        SELECT id, status, voided_at
        FROM donation_allocations
        WHERE id = :allocation_id
    """
    updated_rows = execute_query(fetch_sql, {"allocation_id": allocation_id})
    updated = updated_rows[0]
    
    voided_at_str = str(updated["voided_at"]) if updated["voided_at"] else now
    
    return VoidAllocationResponse(
        id=updated["id"],
        status=updated["status"],
        voided_at=voided_at_str
    )
