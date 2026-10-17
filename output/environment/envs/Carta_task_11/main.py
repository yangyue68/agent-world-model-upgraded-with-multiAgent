import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

app = FastAPI()


class VestingScheduleCreate(BaseModel):
    name: str
    total_vesting_months: int
    cliff_months: int
    vesting_frequency: str


class VestingScheduleResponse(BaseModel):
    id: int
    name: str
    total_vesting_months: int
    cliff_months: int
    vesting_frequency: str
    created_at: datetime


class GrantUpdateRequest(BaseModel):
    vesting_schedule_id: int


class GrantUpdateResponse(BaseModel):
    id: int
    grant_id: int
    vesting_schedule_id: int
    updated_at: datetime


@app.post("/vesting_schedules", response_model=VestingScheduleResponse, operation_id="create_vesting_schedule")
def create_vesting_schedule(payload: VestingScheduleCreate):
    # Validate frequency against allowed values in schema CHECK constraint
    if payload.vesting_frequency not in ("monthly", "quarterly", "annually"):
        raise HTTPException(status_code=422, detail="Invalid vesting_frequency")
    
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    with SessionLocal() as db:
        try:
            result = db.execute(
                text("""
                    INSERT INTO vesting_schedules (name, total_vesting_months, cliff_months, vesting_frequency, created_at, updated_at)
                    VALUES (:name, :tvm, :cm, :vf, :now, :now)
                """),
                {
                    "name": payload.name,
                    "tvm": payload.total_vesting_months,
                    "cm": payload.cliff_months,
                    "vf": payload.vesting_frequency,
                    "now": now
                }
            )
            new_id = result.lastrowid
            db.commit()
            
            # Fetch back to ensure consistency and get all fields
            row = db.execute(
                text("SELECT id, name, total_vesting_months, cliff_months, vesting_frequency, created_at FROM vesting_schedules WHERE id = :id"),
                {"id": new_id}
            ).fetchone()
            
            return VestingScheduleResponse(
                id=row.id,
                name=row.name,
                total_vesting_months=row.total_vesting_months,
                cliff_months=row.cliff_months,
                vesting_frequency=row.vesting_frequency,
                created_at=row.created_at
            )
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=str(e))


@app.put("/grants/{grant_id}", response_model=GrantUpdateResponse, operation_id="update_grant_vesting_schedule")
def update_grant_vesting_schedule(
    grant_id: int = Path(...),
    payload: GrantUpdateRequest = Body(...)
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid user context")

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with SessionLocal() as db:
        try:
            # Verify grant exists
            grant_row = db.execute(
                text("SELECT id, company_id, stakeholder_id, vesting_schedule_id FROM grants WHERE id = :gid"),
                {"gid": grant_id}
            ).fetchone()
            
            if not grant_row:
                raise HTTPException(status_code=404, detail="Grant not found")

            # Verify vesting schedule exists
            vs_row = db.execute(
                text("SELECT id FROM vesting_schedules WHERE id = :vsid"),
                {"vsid": payload.vesting_schedule_id}
            ).fetchone()
            
            if not vs_row:
                raise HTTPException(status_code=404, detail="Vesting schedule not found")

            old_vs_id = grant_row.vesting_schedule_id
            
            # Update the grant
            db.execute(
                text("""
                    UPDATE grants 
                    SET vesting_schedule_id = :new_vs_id, updated_at = :now
                    WHERE id = :gid
                """),
                {
                    "new_vs_id": payload.vesting_schedule_id,
                    "gid": grant_id,
                    "now": now
                }
            )
            
            # Log audit trail
            import json
            old_values_json = json.dumps({"vesting_schedule_id": old_vs_id})
            new_values_json = json.dumps({"vesting_schedule_id": payload.vesting_schedule_id})
            
            db.execute(
                text("""
                    INSERT INTO audit_logs (user_id, action, entity_type, entity_id, old_values, new_values, created_at)
                    VALUES (:uid, 'UPDATE', 'grant', :eid, :old_vals, :new_vals, :now)
                """),
                {
                    "uid": current_user_id,
                    "eid": grant_id,
                    "old_vals": old_values_json,
                    "new_vals": new_values_json,
                    "now": now
                }
            )
            
            db.commit()
            
            # Return response matching spec structure
            # The spec says response has: id, grant_id, vesting_schedule_id, updated_at
            # It seems 'id' here refers to the audit log entry or perhaps a mapping ID? 
            # Looking at typical patterns for such updates where no join table is explicitly returned but implied:
            # However, the prompt asks to preserve original grant date/exercise price.
            # Let's look closely at the response model definition provided in api_spec:
            # properties: id, grant_id, vesting_schedule_id, updated_at
            # This looks like it might be returning the Audit Log ID as 'id'? Or maybe there's an implicit link table?
            # Given the DB schema, there is no separate link table between grants and schedules other than FK on grants.
            # But often these APIs return the ID of the record that was modified/created in relation to the change.
            # Since we inserted into audit_logs, let's assume 'id' in response refers to the audit_log.id which tracks this specific update event.
            # Alternatively, if 'id' meant grant.id, it would be redundant with grant_id.
            # Let's fetch the last inserted audit log ID.
            
            audit_id_result = db.execute(text("SELECT last_insert_rowid()")).scalar()
            
            return GrantUpdateResponse(
                id=audit_id_result,
                grant_id=grant_id,
                vesting_schedule_id=payload.vesting_schedule_id,
                updated_at=now
            )
            
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=str(e))
