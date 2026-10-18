import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class UpdateStatusRequest(BaseModel):
    status: str = Field(..., description="New status to set for the package")
    scanned_at: str = Field(..., description="Timestamp when the package was scanned")
    carrier_code: str = Field(..., description="Carrier code associated with the package")
    signature_id: Optional[str] = Field(None, description="ID of the delivery signature confirmation")


class UpdateStatusResponse(BaseModel):
    package_id: int
    tracking_number: str
    new_status: str
    history_entry_id: int


def parse_iso8601(timestamp_str: str) -> datetime:
    """Parse ISO-8601 timestamp, handling both 'T...Z' and space-separated forms."""
    ts = timestamp_str.strip()
    # Handle Z suffix
    if ts.endswith('Z'):
        ts = ts[:-1] + '+00:00'
    # Try parsing with fromisoformat (Python 3.7+)
    try:
        dt = datetime.fromisoformat(ts)
        return dt
    except ValueError:
        pass
    # Fallback: replace T with space and try again
    ts_alt = ts.replace('T', ' ')
    try:
        dt = datetime.fromisoformat(ts_alt)
        return dt
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {timestamp_str}")


@app.put("/packages/{tracking_number}/status", operation_id="update_package_status")
def update_package_status(
    tracking_number: str = Path(..., description="Unique tracking number of the package"),
    request_body: UpdateStatusRequest = Body(...)
):
    db = SessionLocal()
    try:
        # Find the package by tracking number
        result = db.execute(
            text("SELECT id, current_status FROM packages WHERE tracking_number = :tn"),
            {"tn": tracking_number}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Package not found")

        package_id = result[0]
        old_status = result[1]

        # Parse the scanned_at timestamp
        scanned_dt = parse_iso8601(request_body.scanned_at)
        # Format as SQLite-compatible DATETIME string
        scanned_at_str = scanned_dt.strftime("%Y-%m-%d %H:%M:%S")

        # Validate carrier_code exists in carriers table
        carrier_result = db.execute(
            text("SELECT id FROM carriers WHERE code = :cc"),
            {"cc": request_body.carrier_code}
        ).fetchone()

        if not carrier_result:
            raise HTTPException(status_code=400, detail=f"Unknown carrier code: {request_body.carrier_code}")

        # Update the package's current_status
        db.execute(
            text("""
                UPDATE packages 
                SET current_status = :new_status, updated_at = CURRENT_TIMESTAMP 
                WHERE id = :pid
            """),
            {"new_status": request_body.status, "pid": package_id}
        )

        # Insert into package_status_history
        insert_result = db.execute(
            text("""
                INSERT INTO package_status_history 
                (package_id, status, scanned_at, carrier_code, signature_id, created_at, updated_at)
                VALUES (:pid, :status, :scanned_at, :carrier_code, :signature_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "pid": package_id,
                "status": request_body.status,
                "scanned_at": scanned_at_str,
                "carrier_code": request_body.carrier_code,
                "signature_id": request_body.signature_id
            }
        )

        db.commit()

        # Get the newly inserted history entry ID
        history_entry_id = insert_result.lastrowid

        return UpdateStatusResponse(
            package_id=package_id,
            tracking_number=tracking_number,
            new_status=request_body.status,
            history_entry_id=history_entry_id
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
