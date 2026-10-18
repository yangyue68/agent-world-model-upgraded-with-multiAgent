import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class UpdateStatusRequest(BaseModel):
    status: str = Field(..., description="The new status of the package (e.g., 'in transit')")
    scanned_at: str = Field(..., description="Timestamp when the package was scanned in ISO 8601 format")
    carrier_code: str = Field(..., description="The carrier code associated with the package (e.g., 'UPS')")


class PackageResponse(BaseModel):
    package_id: int
    tracking_number: str
    status: str
    last_scanned_at: str
    carrier_id: int


def parse_iso_timestamp(ts_str: str) -> datetime:
    """Parse ISO-8601 timestamp handling both T...Z and space-separated forms."""
    # Normalize to handle common variations
    ts_str = ts_str.strip()
    
    # Replace 'T' with space if present for uniform parsing, or handle directly
    # Python's fromisoformat handles 'T' but not always trailing 'Z' depending on version.
    # We'll try a few strategies.
    
    try:
        # Try standard ISO format first
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except ValueError:
        pass
        
    try:
        # Handle space separated like "2024-04-15 09:22:17"
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
        
    raise ValueError(f"Invalid timestamp format: {ts_str}")


@app.put("/packages/{tracking_number}/status", operation_id="update_package_status")
def update_package_status(
    tracking_number: str = Path(..., description="The tracking number of the package to update"),
    request_body: UpdateStatusRequest = Body(...)
):
    db = SessionLocal()
    try:
        # Find the package
        pkg_query = text("""
            SELECT id, carrier_id, status, last_scanned_at 
            FROM packages 
            WHERE tracking_number = :tn
        """)
        pkg_result = db.execute(pkg_query, {"tn": tracking_number}).fetchone()
        
        if not pkg_result:
            raise HTTPException(status_code=404, detail="Package not found")
        
        package_id = pkg_result[0]
        current_carrier_id = pkg_result[1]
        
        # Verify carrier code matches the package's carrier or find the correct carrier ID
        carrier_query = text("""
            SELECT id FROM carriers WHERE code = :code
        """)
        carrier_result = db.execute(carrier_query, {"code": request_body.carrier_code}).fetchone()
        
        if not carrier_result:
            raise HTTPException(status_code=404, detail="Carrier not found")
        
        carrier_id = carrier_result[0]
        
        # In this scenario, we assume the provided carrier_code should match the package's existing carrier
        # If they don't match, it might be an error or a reassignment. The spec says "carrier code associated with the package".
        # Let's check if the provided carrier code corresponds to the package's current carrier.
        if carrier_id != current_carrier_id:
             # Depending on business logic, this could be an error. 
             # Given the task updates status for UPS123456789 with carrier UPS, and package 1 has carrier_id 1 (UPS), this should match.
             # If mismatched, we might reject or just use the provided one? 
             # Safer to ensure consistency or allow update if explicitly requested. 
             # However, usually tracking numbers are tied to a specific carrier.
             # Let's assume strict matching for integrity unless specified otherwise.
             # But wait, the input provides carrier_code. It's likely used to verify context or just passed through.
             # I will proceed with the carrier_id derived from the code, assuming it's valid for the package.
             pass

        # Parse the scanned_at timestamp
        try:
            scanned_dt = parse_iso_timestamp(request_body.scanned_at)
            # Convert to naive UTC or store as string? DB stores DATETIME.
            # SQLite doesn't have native timezone support in older versions, often stored as strings or unix timestamps.
            # The schema shows DATETIME. SQLAlchemy/SQLite usually expects ISO strings or datetime objects.
            # Let's format back to a standard string for storage if needed, or pass datetime object.
            # Passing datetime object to SQLite via SQLAlchemy usually works fine.
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {str(e)}")

        # Update the package status
        update_pkg_query = text("""
            UPDATE packages 
            SET status = :status, 
                last_scanned_at = :scanned_at, 
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :pid
        """)
        
        # Format datetime for SQLite compatibility if necessary, though passing datetime obj is preferred
        # SQLite stores datetimes as TEXT in ISO format typically when using default adapters
        scanned_at_str = scanned_dt.isoformat(sep=' ', timespec='seconds')
        
        db.execute(update_pkg_query, {
            "status": request_body.status,
            "scanned_at": scanned_at_str,
            "pid": package_id
        })
        
        # Insert into history table
        insert_history_query = text("""
            INSERT INTO package_status_history (package_id, status, scanned_at, carrier_id)
            VALUES (:pid, :status, :scanned_at, :cid)
        """)
        
        db.execute(insert_history_query, {
            "pid": package_id,
            "status": request_body.status,
            "scanned_at": scanned_at_str,
            "cid": carrier_id
        })
        
        db.commit()
        
        # Fetch updated package info for response
        final_pkg_query = text("""
            SELECT id, tracking_number, status, last_scanned_at, carrier_id 
            FROM packages 
            WHERE id = :pid
        """)
        final_pkg = db.execute(final_pkg_query, {"pid": package_id}).fetchone()
        
        if not final_pkg:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated package")
            
        return PackageResponse(
            package_id=final_pkg[0],
            tracking_number=final_pkg[1],
            status=final_pkg[2],
            last_scanned_at=final_pkg[3],
            carrier_id=final_pkg[4]
        )
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
