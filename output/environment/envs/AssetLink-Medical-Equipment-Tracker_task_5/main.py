import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class DeactivateRequest(BaseModel):
    deactivation_reason: str


class EquipmentResponse(BaseModel):
    id: int
    asset_tag: str
    serial_number: str
    model_id: int
    location_id: int
    status: str


class MaintenanceScheduleResponse(BaseModel):
    id: int
    equipment_id: int
    schedule_type: str
    frequency_days: int | None
    last_performed_at: str | None
    next_due_at: str | None
    is_active: int


class DeactivateResponse(BaseModel):
    message: str
    audit_note_id: int


@app.get(
    "/equipment/by-asset-tag/{asset_tag}",
    operation_id="get_equipment_by_asset_tag",
    response_model=EquipmentResponse,
)
def get_equipment_by_asset_tag(asset_tag: str = Path(...)):
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT id, asset_tag, serial_number, model_id, location_id, status "
                "FROM equipment WHERE asset_tag = :asset_tag"
            ),
            {"asset_tag": asset_tag},
        ).fetchone()
        if result is None:
            raise HTTPException(status_code=404, detail="Equipment not found")
        return EquipmentResponse(
            id=result[0],
            asset_tag=result[1],
            serial_number=result[2],
            model_id=result[3],
            location_id=result[4],
            status=result[5],
        )


@app.get(
    "/equipment/{equipment_id}/maintenance-schedules",
    operation_id="search_maintenance_schedules",
    response_model=list[MaintenanceScheduleResponse],
)
def search_maintenance_schedules(
    equipment_id: int = Path(...),
    schedule_type: str = Query(...),
):
    with engine.connect() as conn:
        # Verify equipment exists
        eq = conn.execute(
            text("SELECT id FROM equipment WHERE id = :eid"),
            {"eid": equipment_id},
        ).fetchone()
        if eq is None:
            raise HTTPException(status_code=404, detail="Equipment not found")

        rows = conn.execute(
            text(
                "SELECT id, equipment_id, schedule_type, frequency_days, "
                "last_performed_at, next_due_at, is_active "
                "FROM maintenance_schedules "
                "WHERE equipment_id = :eid AND schedule_type = :stype"
            ),
            {"eid": equipment_id, "stype": schedule_type},
        ).fetchall()

        results = []
        for row in rows:
            results.append(
                MaintenanceScheduleResponse(
                    id=row[0],
                    equipment_id=row[1],
                    schedule_type=row[2],
                    frequency_days=row[3],
                    last_performed_at=str(row[4]) if row[4] else None,
                    next_due_at=str(row[5]) if row[5] else None,
                    is_active=row[6],
                )
            )
        return results


@app.put(
    "/equipment/{equipment_id}/maintenance-schedules/{schedule_id}/deactivate",
    operation_id="deactivate_maintenance_schedule",
    response_model=DeactivateResponse,
)
def deactivate_maintenance_schedule(
    equipment_id: int = Path(...),
    schedule_id: int = Path(...),
    body: DeactivateRequest = ...,
):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user context")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user context")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        # Verify the schedule belongs to the equipment and is active
        sched = conn.execute(
            text(
                "SELECT id, equipment_id, is_active FROM maintenance_schedules "
                "WHERE id = :sid AND equipment_id = :eid"
            ),
            {"sid": schedule_id, "eid": equipment_id},
        ).fetchone()
        if sched is None:
            raise HTTPException(status_code=404, detail="Maintenance schedule not found")
        if sched[2] != 1:
            raise HTTPException(status_code=400, detail="Schedule is already inactive")

        # Deactivate the schedule
        conn.execute(
            text(
                "UPDATE maintenance_schedules "
                "SET is_active = 0, deactivated_at = :now, deactivation_reason = :reason, updated_at = :now "
                "WHERE id = :sid"
            ),
            {"now": now, "reason": body.deactivation_reason, "sid": schedule_id},
        )

        # Insert audit note
        result = conn.execute(
            text(
                "INSERT INTO audit_notes (equipment_id, maintenance_schedule_id, user_id, note, created_at) "
                "VALUES (:eid, :sid, :uid, :note, :now)"
            ),
            {
                "eid": equipment_id,
                "sid": schedule_id,
                "uid": user_id,
                "note": body.deactivation_reason,
                "now": now,
            },
        )
        audit_note_id = result.lastrowid

    return DeactivateResponse(
        message="Preventive maintenance schedule deactivated successfully.",
        audit_note_id=audit_note_id,
    )
