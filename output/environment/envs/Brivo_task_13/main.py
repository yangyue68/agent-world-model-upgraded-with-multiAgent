import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models for Requests ---

class CreateSiteRequest(BaseModel):
    name: str
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    postal_code: Optional[str] = None


class CreateDoorRequest(BaseModel):
    site_id: int
    name: str
    description: Optional[str] = None


class CreateEmployeeRequest(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: Optional[str] = None


class CreateAccessGroupRequest(BaseModel):
    site_id: int
    name: str
    description: Optional[str] = None


class CreateAccessGroupScheduleRequest(BaseModel):
    access_group_id: int
    day_of_week: int
    start_time: str
    end_time: str


class AssignDoorToAccessGroupRequest(BaseModel):
    access_group_id: int
    door_id: int


class AssignEmployeeToAccessGroupRequest(BaseModel):
    employee_id: int
    access_group_id: int


class CreateBadgeRequest(BaseModel):
    employee_id: int
    credential_number: str
    status: Optional[str] = "active"
    expires_at: Optional[str] = None


# --- Helper Functions ---

def get_current_timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def execute_insert(table: str, data: dict) -> int:
    """Inserts a row into the specified table and returns the new ID."""
    columns = ", ".join(data.keys())
    placeholders = ", :".join(data.keys())
    query = f"INSERT INTO {table} ({columns}) VALUES (:{placeholders}) RETURNING id"
    
    with engine.connect() as conn:
        result = conn.execute(text(query), data)
        row = result.fetchone()
        conn.commit()
        if row:
            return row[0]
        else:
            # Fallback if RETURNING isn't supported or fails, though SQLite 3.35+ supports it
            # For safety in this context, we assume standard behavior or fetch max id if needed
            # But since we are using SQLAlchemy Core/Text, let's rely on the insert returning ID
            # If the above fails due to older driver issues, we might need a different approach.
            # However, modern SQLAlchemy + SQLite usually handles this well.
            pass
    
    # Alternative robust way if RETURNING fails in some environments:
    # Insert without returning, then select last inserted rowid
    cols_str = ", ".join(data.keys())
    vals_placeholders = ", ".join([f":{k}" for k in data.keys()])
    insert_query = f"INSERT INTO {table} ({cols_str}) VALUES ({vals_placeholders})"
    
    with engine.connect() as conn:
        res = conn.execute(text(insert_query), data)
        conn.commit()
        # Get the last inserted rowid
        rowid_res = conn.execute(text("SELECT last_insert_rowid()"))
        new_id = rowid_res.scalar()
        return new_id


def execute_insert_no_returning(table: str, data: dict):
    """Inserts a row into the specified table. Used for composite keys or when ID isn't primary autoincrement."""
    columns = ", ".join(data.keys())
    placeholders = ", ".join([f":{k}" for k in data.keys()])
    query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
    
    with engine.connect() as conn:
        conn.execute(text(query), data)
        conn.commit()


def fetch_site(site_id: int) -> dict:
    query = "SELECT * FROM sites WHERE id = :site_id"
    with engine.connect() as conn:
        result = conn.execute(text(query), {"site_id": site_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Site not found")
        return dict(row._mapping)


def fetch_door(door_id: int) -> dict:
    query = "SELECT * FROM doors WHERE id = :door_id"
    with engine.connect() as conn:
        result = conn.execute(text(query), {"door_id": door_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Door not found")
        return dict(row._mapping)


def fetch_employee(employee_id: int) -> dict:
    query = "SELECT * FROM employees WHERE id = :employee_id"
    with engine.connect() as conn:
        result = conn.execute(text(query), {"employee_id": employee_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Employee not found")
        return dict(row._mapping)


def fetch_access_group(group_id: int) -> dict:
    query = "SELECT * FROM access_groups WHERE id = :group_id"
    with engine.connect() as conn:
        result = conn.execute(text(query), {"group_id": group_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Access Group not found")
        return dict(row._mapping)


def fetch_schedule(schedule_id: int) -> dict:
    query = "SELECT * FROM access_group_schedules WHERE id = :schedule_id"
    with engine.connect() as conn:
        result = conn.execute(text(query), {"schedule_id": schedule_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return dict(row._mapping)


def fetch_badge(badge_id: int) -> dict:
    query = "SELECT * FROM badges WHERE id = :badge_id"
    with engine.connect() as conn:
        result = conn.execute(text(query), {"badge_id": badge_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Badge not found")
        return dict(row._mapping)


# --- Endpoints ---

@app.post("/sites", operation_id="create_site")
def create_site(request: CreateSiteRequest):
    now = get_current_timestamp()
    data = {
        "name": request.name,
        "address_line1": request.address_line1,
        "address_line2": request.address_line2,
        "city": request.city,
        "state": request.state,
        "postal_code": request.postal_code,
        "country": "USA",
        "created_at": now,
        "updated_at": now
    }
    
    try:
        new_id = execute_insert("sites", data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    site = fetch_site(new_id)
    return site


@app.post("/doors", operation_id="create_door")
def create_door(request: CreateDoorRequest):
    # Verify site exists
    fetch_site(request.site_id)
    
    now = get_current_timestamp()
    data = {
        "site_id": request.site_id,
        "name": request.name,
        "description": request.description,
        "created_at": now,
        "updated_at": now
    }
    
    try:
        new_id = execute_insert("doors", data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    door = fetch_door(new_id)
    return door


@app.post("/employees", operation_id="create_employee")
def create_employee(request: CreateEmployeeRequest):
    now = get_current_timestamp()
    data = {
        "first_name": request.first_name,
        "last_name": request.last_name,
        "email": request.email,
        "phone": request.phone,
        "created_at": now,
        "updated_at": now
    }
    
    try:
        new_id = execute_insert("employees", data)
    except Exception as e:
        # Handle unique constraint violation specifically if needed, but generic 400 is fine per spec style
        raise HTTPException(status_code=400, detail=str(e))
        
    employee = fetch_employee(new_id)
    return employee


@app.post("/access_groups", operation_id="create_access_group")
def create_access_group(request: CreateAccessGroupRequest):
    # Verify site exists
    fetch_site(request.site_id)
    
    now = get_current_timestamp()
    data = {
        "site_id": request.site_id,
        "name": request.name,
        "description": request.description,
        "created_at": now,
        "updated_at": now
    }
    
    try:
        new_id = execute_insert("access_groups", data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    group = fetch_access_group(new_id)
    return group


@app.post("/access_group_schedules", operation_id="create_access_group_schedule")
def create_access_group_schedule(request: CreateAccessGroupScheduleRequest):
    # Verify access group exists
    fetch_access_group(request.access_group_id)
    
    # Validate day of week range based on schema CHECK constraint logic (though DB will enforce)
    if not (0 <= request.day_of_week <= 6):
        raise HTTPException(status_code=400, detail="day_of_week must be between 0 and 6")

    now = get_current_timestamp()
    data = {
        "access_group_id": request.access_group_id,
        "day_of_week": request.day_of_week,
        "start_time": request.start_time,
        "end_time": request.end_time,
        "created_at": now,
        "updated_at": now
    }
    
    try:
        new_id = execute_insert("access_group_schedules", data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    schedule = fetch_schedule(new_id)
    return schedule


@app.post("/access_group_doors", operation_id="assign_door_to_access_group")
def assign_door_to_access_group(request: AssignDoorToAccessGroupRequest):
    # Verify entities exist
    fetch_access_group(request.access_group_id)
    fetch_door(request.door_id)
    
    now = get_current_timestamp()
    data = {
        "access_group_id": request.access_group_id,
        "door_id": request.door_id,
        "created_at": now
    }
    
    try:
        execute_insert_no_returning("access_group_doors", data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    # Return object matching response schema
    return {
        "access_group_id": request.access_group_id,
        "door_id": request.door_id,
        "created_at": now
    }


@app.post("/employee_access_groups", operation_id="assign_employee_to_access_group")
def assign_employee_to_access_group(request: AssignEmployeeToAccessGroupRequest):
    # Verify entities exist
    fetch_employee(request.employee_id)
    fetch_access_group(request.access_group_id)
    
    now = get_current_timestamp()
    data = {
        "employee_id": request.employee_id,
        "access_group_id": request.access_group_id,
        "assigned_at": now,
        "created_at": now,
        "updated_at": now
    }
    
    try:
        execute_insert_no_returning("employee_access_groups", data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    return {
        "employee_id": request.employee_id,
        "access_group_id": request.access_group_id,
        "assigned_at": now,
        "created_at": now,
        "updated_at": now
    }


@app.post("/badges", operation_id="create_badge")
def create_badge(request: CreateBadgeRequest):
    # Verify employee exists
    fetch_employee(request.employee_id)
    
    now = get_current_timestamp()
    
    # Determine status
    status = request.status if request.status else "active"
    
    # Validate status against allowed values from schema
    allowed_statuses = ['active', 'inactive', 'lost', 'stolen', 'expired']
    if status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {allowed_statuses}")

    data = {
        "employee_id": request.employee_id,
        "credential_number": request.credential_number,
        "status": status,
        "issued_at": now,
        "expires_at": request.expires_at,
        "created_at": now,
        "updated_at": now
    }
    
    try:
        new_id = execute_insert("badges", data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    badge = fetch_badge(new_id)
    return badge
