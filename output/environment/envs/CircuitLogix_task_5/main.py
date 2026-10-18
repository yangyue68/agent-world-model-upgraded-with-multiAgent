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
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class UpdateStopStatusRequest(BaseModel):
    new_status: str = Field(..., description="New status to set for the stop (must be 'returned')")
    reason_code: str = Field(..., description="Reason code for the status change (e.g., 'DAMG-07')")
    return_timestamp: str = Field(..., description="Timestamp when the return occurred in ISO format")


class StopStatusResponse(BaseModel):
    success: bool
    message: str


class CreateMaintenanceAlertRequest(BaseModel):
    vehicle_code: str = Field(..., description="Vehicle code for which to create the alert (e.g., 'VL-9876-TRN')")
    alert_type: str = Field(..., description="Type of maintenance alert (e.g., 'battery_low')")
    description: str = Field(..., description="Description of the maintenance issue")
    severity: str = Field(..., description="Severity level of the alert (low, medium, high, critical)")


class MaintenanceAlertResponse(BaseModel):
    alert_id: int
    success: bool


def parse_iso_datetime(ts_str: str) -> datetime:
    """Parse ISO-8601 timestamp handling both T...Z and space-separated forms."""
    ts_str_clean = ts_str.strip()
    if ts_str_clean.endswith('Z'):
        ts_str_clean = ts_str_clean[:-1] + '+00:00'
    
    try:
        dt = datetime.fromisoformat(ts_str_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        # Fallback for some edge cases where format might vary slightly
        # Try replacing space with T if present
        if ' ' in ts_str_clean:
             parts = ts_str_clean.split(' ')
             if len(parts) == 2:
                 combined = f"{parts[0]}T{parts[1]}"
                 if combined.endswith('+00:00'):
                     pass # already handled
                 elif combined.endswith('Z'):
                     combined = combined[:-1] + '+00:00'
                 try:
                    dt = datetime.fromisoformat(combined)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                 except ValueError:
                    pass
        
        raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {ts_str}")


@app.put("/routes/{route_code}/stops/{stop_code}/status", response_model=StopStatusResponse, operation_id="update_stop_status")
async def update_stop_status(
    route_code: str = Path(..., description="Unique identifier for the route"),
    stop_code: str = Path(..., description="Unique identifier for the stop"),
    request_body: UpdateStopStatusRequest = Body(...)
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User context missing")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    if request_body.new_status != "returned":
        raise HTTPException(status_code=400, detail="New status must be 'returned'")

    parsed_ts = parse_iso_datetime(request_body.return_timestamp)
    db_ts_str = parsed_ts.strftime("%Y-%m-%d %H:%M:%S")

    with engine.connect() as conn:
        # Verify Route exists and is in_progress
        route_res = conn.execute(text("""
            SELECT id FROM routes WHERE route_code = :rc AND status = 'in_progress'
        """), {"rc": route_code})
        route_row = route_res.fetchone()
        if not route_row:
            raise HTTPException(status_code=404, detail="Route not found or not in progress")
        route_id = route_row[0]

        # Verify Stop exists, belongs to this route, and is currently 'arrived'
        stop_res = conn.execute(text("""
            SELECT id, status FROM stops WHERE stop_code = :sc AND route_id = :rid
        """), {"sc": stop_code, "rid": route_id})
        stop_row = stop_res.fetchone()
        if not stop_row:
            raise HTTPException(status_code=404, detail="Stop not found on this route")
        
        stop_id = stop_row[0]
        current_status = stop_row[1]

        if current_status != "arrived":
            raise HTTPException(status_code=400, detail=f"Stop status is '{current_status}', cannot transition to 'returned' from here")

        # Execute updates within the implicit transaction managed by the connection
        try:
            # 1. Update Stop Status
            conn.execute(text("""
                UPDATE stops SET status = 'returned', updated_at = CURRENT_TIMESTAMP WHERE id = :sid
            """), {"sid": stop_id})

            # 2. Insert into stop_status_history
            conn.execute(text("""
                INSERT INTO stop_status_history (stop_id, old_status, new_status, reason_code, changed_by, changed_at)
                VALUES (:sid, :old_s, :new_s, :rc, :cb, :cat)
            """), {
                "sid": stop_id,
                "old_s": "arrived",
                "new_s": "returned",
                "rc": request_body.reason_code,
                "cb": current_user_id,
                "cat": db_ts_str
            })

            # 3. Insert into return_events
            conn.execute(text("""
                INSERT INTO return_events (stop_id, reason_code, return_timestamp, recorded_by)
                VALUES (:sid, :rc, :rt, :rb)
            """), {
                "sid": stop_id,
                "rc": request_body.reason_code,
                "rt": db_ts_str,
                "rb": current_user_id
            })
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=str(e))

    return StopStatusResponse(success=True, message="Stop status updated successfully")


@app.post("/maintenance-alerts", response_model=MaintenanceAlertResponse, operation_id="create_maintenance_alert")
async def create_maintenance_alert(
    request_body: CreateMaintenanceAlertRequest = Body(...)
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User context missing")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    valid_severities = ['low', 'medium', 'high', 'critical']
    if request_body.severity not in valid_severities:
        raise HTTPException(status_code=400, detail=f"Invalid severity. Must be one of {valid_severities}")

    with engine.connect() as conn:
        # Find Vehicle ID
        veh_res = conn.execute(text("""
            SELECT id FROM vehicles WHERE vehicle_code = :vc
        """), {"vc": request_body.vehicle_code})
        veh_row = veh_res.fetchone()
        if not veh_row:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        vehicle_id = veh_row[0]

        # Insert Alert
        res = conn.execute(text("""
            INSERT INTO maintenance_alerts (vehicle_id, alert_type, description, severity, created_by)
            VALUES (:vid, :at, :desc, :sev, :cb)
        """), {
            "vid": vehicle_id,
            "at": request_body.alert_type,
            "desc": request_body.description,
            "sev": request_body.severity,
            "cb": current_user_id
        })
        
        alert_id = res.lastrowid
        conn.commit()

    return MaintenanceAlertResponse(alert_id=alert_id, success=True)
