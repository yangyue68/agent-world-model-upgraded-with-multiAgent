import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from contextlib import asynccontextmanager

# Database configuration - DATABASE_PATH is pre-set with full SQLAlchemy URL
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)


class FeeScheduleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    effective_date: Optional[str] = None


class FeeScheduleResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    effective_date: Optional[str]
    is_active: bool


class ProviderResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    specialty: Optional[str]


class AppointmentTypeResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    duration_minutes: int


class BaseFeeItem(BaseModel):
    appointment_type_id: int
    fee_amount: float


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="CareCircle Clinic Manager", lifespan=lifespan)


@app.post("/fee_schedules", response_model=FeeScheduleResponse, operation_id="create_fee_schedule")
def create_fee_schedule(payload: FeeScheduleCreate):
    """
    Create a new fee schedule named '2025 Standard Rates' that applies a 5% increase 
    to all existing fees for appointment types provided by Dr. Sarah Lee and Dr. James Wilson.
    Uses the latest active fee schedule as base for calculations.
    """
    # Find provider IDs for Sarah Lee and James Wilson
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id FROM providers 
                WHERE (first_name = :fn1 AND last_name = :ln1) 
                   OR (first_name = :fn2 AND last_name = :ln2)
            """),
            {
                "fn1": "Sarah", "ln1": "Lee",
                "fn2": "James", "ln2": "Wilson"
            }
        )
        provider_rows = result.fetchall()
    
    if len(provider_rows) < 2:
        raise HTTPException(status_code=404, detail="Required providers not found")
    
    provider_ids = [row[0] for row in provider_rows]
    
    # Get the latest active fee schedule ID
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id FROM fee_schedules 
                WHERE is_active = 1 
                ORDER BY created_at DESC, id DESC 
                LIMIT 1
            """)
        )
        fs_row = result.fetchone()
    
    if not fs_row:
        raise HTTPException(status_code=404, detail="No active fee schedule found")
    
    base_schedule_id = fs_row[0]
    
    # Get appointment type IDs associated with these providers
    placeholders = ", ".join([f":pid_{i}" for i in range(len(provider_ids))])
    params = {f"pid_{i}": pid for i, pid in enumerate(provider_ids)}
    
    with engine.connect() as conn:
        result = conn.execute(
            text(f"""
                SELECT DISTINCT pat.appointment_type_id 
                FROM provider_appointment_types pat
                WHERE pat.provider_id IN ({placeholders})
            """),
            params
        )
        appt_type_rows = result.fetchall()
    
    appointment_type_ids = [row[0] for row in appt_type_rows]
    
    if not appointment_type_ids:
        raise HTTPException(status_code=404, detail="No appointment types found for specified providers")
    
    # Get base fees from the latest active schedule for these appointment types and providers
    at_placeholders = ", ".join([f":at_{i}" for i in range(len(appointment_type_ids))])
    at_params = {f"at_{i}": aid for i, aid in enumerate(appointment_type_ids)}
    at_params.update(params)
    
    with engine.connect() as conn:
        result = conn.execute(
            text(f"""
                SELECT fsi.appointment_type_id, fsi.provider_id, fsi.fee_amount
                FROM fee_schedule_items fsi
                WHERE fsi.fee_schedule_id = :base_sid
                  AND fsi.appointment_type_id IN ({at_placeholders})
                  AND fsi.provider_id IN ({placeholders})
            """),
            {"base_sid": base_schedule_id, **at_params}
        )
        base_fees = result.fetchall()
    
    if not base_fees:
        raise HTTPException(status_code=404, detail="No base fees found for specified providers/appointment types")
    
    # Insert new fee schedule
    with engine.begin() as conn:
        insert_result = conn.execute(
            text("""
                INSERT INTO fee_schedules (name, description, effective_date, is_active)
                VALUES (:name, :description, :effective_date, 1)
            """),
            {
                "name": payload.name,
                "description": payload.description,
                "effective_date": payload.effective_date
            }
        )
        new_schedule_id = insert_result.lastrowid
        
        # Insert fee schedule items with 5% increase
        for row in base_fees:
            aptype_id, prov_id, base_amount = row
            new_amount = round(base_amount * 1.05, 2)
            conn.execute(
                text("""
                    INSERT INTO fee_schedule_items (fee_schedule_id, appointment_type_id, provider_id, fee_amount)
                    VALUES (:fsid, :aptype, :prov, :amount)
                """),
                {
                    "fsid": new_schedule_id,
                    "aptype": aptype_id,
                    "prov": prov_id,
                    "amount": new_amount
                }
            )
        
        # Fetch the created schedule
        result = conn.execute(
            text("""
                SELECT id, name, description, effective_date, is_active 
                FROM fee_schedules 
                WHERE id = :sid
            """),
            {"sid": new_schedule_id}
        )
        sched_row = result.fetchone()
    
    return FeeScheduleResponse(
        id=sched_row[0],
        name=sched_row[1],
        description=sched_row[2],
        effective_date=str(sched_row[3]) if sched_row[3] else None,
        is_active=bool(sched_row[4])
    )


@app.get("/providers", response_model=List[ProviderResponse], operation_id="find_providers")
def find_providers(
    first_name: Optional[str] = Query(None),
    last_name: Optional[str] = Query(None)
):
    conditions = []
    params = {}
    
    if first_name:
        conditions.append("first_name = :fn")
        params["fn"] = first_name
    if last_name:
        conditions.append("last_name = :ln")
        params["ln"] = last_name
    
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    
    with engine.connect() as conn:
        result = conn.execute(
            text(f"""
                SELECT id, first_name, last_name, specialty 
                FROM providers 
                {where_clause}
            """),
            params
        )
        rows = result.fetchall()
    
    return [
        ProviderResponse(id=r[0], first_name=r[1], last_name=r[2], specialty=r[3])
        for r in rows
    ]


@app.get("/appointment_types", response_model=List[AppointmentTypeResponse], operation_id="find_appointment_types")
def find_appointment_types(
    provider_ids: str = Query(...)
):
    try:
        ids = [int(x.strip()) for x in provider_ids.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid provider_ids format")
    
    if not ids:
        return []
    
    placeholders = ", ".join([f":pid_{i}" for i in range(len(ids))])
    params = {f"pid_{i}": pid for i, pid in enumerate(ids)}
    
    with engine.connect() as conn:
        result = conn.execute(
            text(f"""
                SELECT DISTINCT at.id, at.name, at.description, at.duration_minutes
                FROM appointment_types at
                JOIN provider_appointment_types pat ON at.id = pat.appointment_type_id
                WHERE pat.provider_id IN ({placeholders})
            """),
            params
        )
        rows = result.fetchall()
    
    return [
        AppointmentTypeResponse(id=r[0], name=r[1], description=r[2], duration_minutes=r[3])
        for r in rows
    ]


@app.get("/fee_schedules/latest_active", response_model=FeeScheduleResponse, operation_id="get_latest_active_fee_schedule")
def get_latest_active_fee_schedule():
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, name, description, effective_date, is_active 
                FROM fee_schedules 
                WHERE is_active = 1 
                ORDER BY created_at DESC, id DESC 
                LIMIT 1
            """)
        )
        row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="No active fee schedule found")
    
    return FeeScheduleResponse(
        id=row[0],
        name=row[1],
        description=row[2],
        effective_date=str(row[3]) if row[3] else None,
        is_active=bool(row[4])
    )


@app.get("/fee_schedule_items/base_fees", response_model=List[BaseFeeItem], operation_id="get_base_fees_for_providers")
def get_base_fees_for_providers(
    provider_ids: str = Query(...)
):
    try:
        ids = [int(x.strip()) for x in provider_ids.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid provider_ids format")
    
    if not ids:
        return []
    
    # Get latest active fee schedule
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id FROM fee_schedules 
                WHERE is_active = 1 
                ORDER BY created_at DESC, id DESC 
                LIMIT 1
            """)
        )
        fs_row = result.fetchone()
    
    if not fs_row:
        raise HTTPException(status_code=404, detail="No active fee schedule found")
    
    base_schedule_id = fs_row[0]
    
    placeholders = ", ".join([f":pid_{i}" for i in range(len(ids))])
    params = {f"pid_{i}": pid for i, pid in enumerate(ids)}
    params["sid"] = base_schedule_id
    
    with engine.connect() as conn:
        result = conn.execute(
            text(f"""
                SELECT fsi.appointment_type_id, fsi.fee_amount
                FROM fee_schedule_items fsi
                WHERE fsi.fee_schedule_id = :sid
                  AND fsi.provider_id IN ({placeholders})
            """),
            params
        )
        rows = result.fetchall()
    
    return [
        BaseFeeItem(appointment_type_id=r[0], fee_amount=float(r[1]))
        for r in rows
    ]
