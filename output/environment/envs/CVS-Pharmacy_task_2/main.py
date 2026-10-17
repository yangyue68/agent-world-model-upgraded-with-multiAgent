import os
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AutoRefillRequest(BaseModel):
    interval_days: int
    email_notifications_enabled: bool


@app.post("/prescriptions/{prescription_id}/transfer", operation_id="transfer_prescription")
def transfer_prescription(
    prescription_id: int,
    from_pharmacy_nabp: str = Query(..., description="NABP number of the source pharmacy"),
    to_pharmacy_nabp: str = Query(..., description="NABP number of the destination pharmacy"),
):
    with engine.connect() as conn:
        # Verify prescription exists
        rx_row = conn.execute(
            text("SELECT id, status FROM prescriptions WHERE id = :pid"),
            {"pid": prescription_id},
        ).fetchone()
        if not rx_row:
            raise HTTPException(status_code=404, detail="Prescription not found")

        # Look up source pharmacy
        src_row = conn.execute(
            text("SELECT id FROM pharmacies WHERE nabp_number = :nabp"),
            {"nabp": from_pharmacy_nabp},
        ).fetchone()
        if not src_row:
            raise HTTPException(status_code=404, detail=f"Source pharmacy with NABP {from_pharmacy_nabp} not found")
        from_pharmacy_id = src_row[0]

        # Look up destination pharmacy
        dst_row = conn.execute(
            text("SELECT id FROM pharmacies WHERE nabp_number = :nabp"),
            {"nabp": to_pharmacy_nabp},
        ).fetchone()
        if not dst_row:
            raise HTTPException(status_code=404, detail=f"Destination pharmacy with NABP {to_pharmacy_nabp} not found")
        to_pharmacy_id = dst_row[0]

        # Insert transfer record
        result = conn.execute(
            text("""
                INSERT INTO prescription_transfers (prescription_id, from_pharmacy_id, to_pharmacy_id, status)
                VALUES (:pid, :fpid, :tpid, 'completed')
            """),
            {"pid": prescription_id, "fpid": from_pharmacy_id, "tpid": to_pharmacy_id},
        )
        transfer_id = result.lastrowid

        # Update prescription status to transferred
        conn.execute(
            text("UPDATE prescriptions SET status = 'transferred', updated_at = CURRENT_TIMESTAMP WHERE id = :pid"),
            {"pid": prescription_id},
        )

        conn.commit()

    return {
        "transfer_id": transfer_id,
        "prescription_id": prescription_id,
        "status": "transferred",
    }


@app.post("/prescriptions/{prescription_id}/auto-refill", operation_id="setup_auto_refill")
def setup_auto_refill(prescription_id: int, body: AutoRefillRequest):
    with engine.connect() as conn:
        # Verify prescription exists
        rx_row = conn.execute(
            text("SELECT id, status FROM prescriptions WHERE id = :pid"),
            {"pid": prescription_id},
        ).fetchone()
        if not rx_row:
            raise HTTPException(status_code=404, detail="Prescription not found")

        current_status = rx_row[1]

        # Calculate next refill date
        today = date.today()
        next_refill_date = today + timedelta(days=body.interval_days)

        email_flag = 1 if body.email_notifications_enabled else 0

        # Check if a schedule already exists for this prescription
        existing = conn.execute(
            text("SELECT id FROM auto_refill_schedules WHERE prescription_id = :pid"),
            {"pid": prescription_id},
        ).fetchone()

        if existing:
            schedule_id = existing[0]
            conn.execute(
                text("""
                    UPDATE auto_refill_schedules
                    SET interval_days = :interval,
                        next_refill_date = :next_date,
                        email_notifications_enabled = :email,
                        is_active = 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :sid
                """),
                {
                    "interval": body.interval_days,
                    "next_date": next_refill_date.isoformat(),
                    "email": email_flag,
                    "sid": schedule_id,
                },
            )
        else:
            result = conn.execute(
                text("""
                    INSERT INTO auto_refill_schedules (prescription_id, interval_days, next_refill_date, email_notifications_enabled, is_active)
                    VALUES (:pid, :interval, :next_date, :email, 1)
                """),
                {
                    "pid": prescription_id,
                    "interval": body.interval_days,
                    "next_date": next_refill_date.isoformat(),
                    "email": email_flag,
                },
            )
            schedule_id = result.lastrowid

        # If prescription was inactive/transferred/expired/cancelled, update to active per spec
        if current_status != "active":
            conn.execute(
                text("UPDATE prescriptions SET status = 'active', updated_at = CURRENT_TIMESTAMP WHERE id = :pid"),
                {"pid": prescription_id},
            )

        conn.commit()

    return {
        "schedule_id": schedule_id,
        "prescription_id": prescription_id,
        "interval_days": body.interval_days,
        "next_refill_date": next_refill_date.isoformat(),
        "email_notifications_enabled": body.email_notifications_enabled,
    }
