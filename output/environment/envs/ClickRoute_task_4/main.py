import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CancelShipmentRequest(BaseModel):
    reason: str


class CancelShipmentResponse(BaseModel):
    success: bool
    message: str
    shipment_id: int


@app.put(
    "/shipments/{tracking_number}/cancel",
    operation_id="cancel_shipment",
    response_model=CancelShipmentResponse,
)
def cancel_shipment(
    tracking_number: str = Path(...),
    body: CancelShipmentRequest = ...,
):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="No authenticated user")
    try:
        current_user_id = int(current_user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid user id")

    with engine.connect() as conn:
        # Find the shipment by tracking number
        result = conn.execute(
            text("SELECT id, status, current_exception_code FROM shipments WHERE tracking_number = :tn"),
            {"tn": tracking_number},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Shipment not found")

        shipment_id = row[0]
        status = row[1]
        exception_code = row[2]

        # Validate that the shipment has an active ADDR_CORRECTION exception
        if exception_code != "ADDR_CORRECTION":
            raise HTTPException(
                status_code=400,
                detail="Shipment does not have an active ADDR_CORRECTION exception",
            )

        # Check if already cancelled
        if status == "cancelled":
            raise HTTPException(status_code=400, detail="Shipment is already cancelled")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Update shipment status to cancelled
        conn.execute(
            text("""
                UPDATE shipments
                SET status = 'cancelled',
                    cancelled_at = :now,
                    cancellation_reason = :reason,
                    updated_at = :now
                WHERE id = :sid
            """),
            {"now": now, "reason": body.reason, "sid": shipment_id},
        )

        # Create a cancellation record
        conn.execute(
            text("""
                INSERT INTO cancellations (shipment_id, reason, cancelled_by, cancelled_at, created_at)
                VALUES (:sid, :reason, :uid, :now, :now)
            """),
            {"sid": shipment_id, "reason": body.reason, "uid": current_user_id, "now": now},
        )

        # Create a shipment event for the cancellation
        conn.execute(
            text("""
                INSERT INTO shipment_events (shipment_id, event_type, event_code, description, occurred_at, created_by, created_at)
                VALUES (:sid, 'cancellation', 'CANCELLED', :desc, :now, :uid, :now)
            """),
            {
                "sid": shipment_id,
                "desc": f"Shipment cancelled: {body.reason}",
                "now": now,
                "uid": current_user_id,
            },
        )

        # Resolve the open ADDR_CORRECTION exception
        conn.execute(
            text("""
                UPDATE exceptions
                SET status = 'resolved',
                    resolved_at = :now,
                    resolution_notes = :notes,
                    updated_at = :now
                WHERE shipment_id = :sid AND exception_code = 'ADDR_CORRECTION' AND status = 'open'
            """),
            {
                "now": now,
                "notes": f"Cancelled due to: {body.reason}",
                "sid": shipment_id,
            },
        )

        conn.commit()

    return CancelShipmentResponse(
        success=True,
        message=f"Shipment {tracking_number} has been cancelled successfully.",
        shipment_id=shipment_id,
    )
