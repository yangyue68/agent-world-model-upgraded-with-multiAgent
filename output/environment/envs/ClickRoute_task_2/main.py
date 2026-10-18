import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UpdateStatusRequest(BaseModel):
    status: str
    scanned_at: str
    carrier_code: str
    exception_note: Optional[str] = None


class UpdateStatusResponse(BaseModel):
    package_id: int
    status: str
    scanned_at: str
    carrier_code: str
    exception_note: Optional[str] = None


def parse_timestamp(ts: str) -> str:
    """Parse ISO-8601 timestamp and return SQLite-compatible format."""
    # Handle both 'T...Z' and space-separated forms
    ts = ts.replace("Z", "+00:00")
    if "T" in ts:
        dt = datetime.fromisoformat(ts)
    else:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@app.put(
    "/packages/{tracking_number}/status",
    operation_id="update_package_status",
    response_model=UpdateStatusResponse,
)
def update_package_status(
    tracking_number: str = Path(...),
    request: UpdateStatusRequest = Body(...),
):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=500, detail="Current user ID not available")

    try:
        current_user_id_int = int(current_user_id)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID")

    with engine.connect() as conn:
        # Find the package by tracking number
        result = conn.execute(
            text("SELECT id, carrier_id FROM packages WHERE tracking_number = :tn"),
            {"tn": tracking_number},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Package not found")

        package_id = row[0]
        carrier_id = row[1]

        # Verify carrier code matches the package's carrier
        carrier_result = conn.execute(
            text("SELECT code FROM carriers WHERE id = :cid"),
            {"cid": carrier_id},
        )
        carrier_row = carrier_result.fetchone()
        if not carrier_row or carrier_row[0] != request.carrier_code:
            raise HTTPException(
                status_code=400,
                detail="Carrier code does not match package carrier",
            )

        # Parse and normalize the scanned_at timestamp
        try:
            normalized_scanned_at = parse_timestamp(request.scanned_at)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid scanned_at timestamp")

        # Update the package status
        conn.execute(
            text(
                "UPDATE packages SET status = :status, updated_at = CURRENT_TIMESTAMP WHERE id = :pid"
            ),
            {"status": request.status, "pid": package_id},
        )

        # Insert a new package_status_event
        conn.execute(
            text(
                """INSERT INTO package_status_events 
                   (package_id, status, scanned_at, carrier_code, exception_note, created_by)
                   VALUES (:pid, :status, :scanned_at, :carrier_code, :exception_note, :created_by)"""
            ),
            {
                "pid": package_id,
                "status": request.status,
                "scanned_at": normalized_scanned_at,
                "carrier_code": request.carrier_code,
                "exception_note": request.exception_note,
                "created_by": current_user_id_int,
            },
        )

        conn.commit()

    return UpdateStatusResponse(
        package_id=package_id,
        status=request.status,
        scanned_at=normalized_scanned_at,
        carrier_code=request.carrier_code,
        exception_note=request.exception_note,
    )
