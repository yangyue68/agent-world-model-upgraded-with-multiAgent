import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="BookMyGarage API")


class CancelAppointmentResponse(BaseModel):
    appointment_id: int
    new_status: str
    refund_processed: bool
    notification_sent: bool
    waitlisted_user_id: Optional[int]
    refund_amount: float


@app.post(
    "/appointments/{appointment_id}/cancel",
    operation_id="cancel_appointment",
    response_model=CancelAppointmentResponse,
)
def cancel_appointment(appointment_id: int = Path(..., description="Internal ID of the appointment to cancel")):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    db = SessionLocal()
    try:
        # 1. Fetch the appointment and verify ownership/status
        appt_row = db.execute(
            text("""
                SELECT id, user_id, shop_id, service_id, appointment_datetime, status, total_price 
                FROM appointments 
                WHERE id = :id
            """),
            {"id": appointment_id}
        ).fetchone()

        if not appt_row:
            raise HTTPException(status_code=404, detail="Appointment not found")
        
        if appt_row.user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Forbidden: You can only cancel your own appointments")
        
        if appt_row.status != 'booked':
            raise HTTPException(status_code=400, detail=f"Cannot cancel appointment with status '{appt_row.status}'")

        # 2. Calculate Refund
        original_amount = appt_row.total_price
        cancellation_fee = 5.0
        refund_amount = max(0.0, original_amount - cancellation_fee)

        # 3. Update Appointment Status to 'vacated'
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            text("""
                UPDATE appointments 
                SET status = 'vacated', updated_at = :now 
                WHERE id = :id
            """),
            {"id": appointment_id, "now": now_utc}
        )

        # 4. Create Refund Record
        db.execute(
            text("""
                INSERT INTO refunds (appointment_id, user_id, original_amount, cancellation_fee, refund_amount, status, processed_at, created_at, updated_at)
                VALUES (:appt_id, :user_id, :orig_amt, :fee, :ref_amt, 'processed', :now, :now, :now)
            """),
            {
                "appt_id": appointment_id,
                "user_id": current_user_id,
                "orig_amt": original_amount,
                "fee": cancellation_fee,
                "ref_amt": refund_amount,
                "now": now_utc
            }
        )

        # 5. Find Top Waitlisted User for this slot
        # Match shop_id, service_id, and desired_datetime exactly
        waitlist_row = db.execute(
            text("""
                SELECT id, user_id, position 
                FROM waitlist 
                WHERE shop_id = :shop_id 
                  AND service_id = :service_id 
                  AND desired_datetime = :desired_dt 
                  AND status = 'waiting'
                ORDER BY position ASC 
                LIMIT 1
            """),
            {
                "shop_id": appt_row.shop_id,
                "service_id": appt_row.service_id,
                "desired_dt": appt_row.appointment_datetime
            }
        ).fetchone()

        notification_sent = False
        waitlisted_user_id = None

        if waitlist_row:
            waitlisted_user_id = waitlist_row.user_id
            
            # Update waitlist entry to 'notified'
            db.execute(
                text("""
                    UPDATE waitlist 
                    SET status = 'notified', updated_at = :now 
                    WHERE id = :wl_id
                """),
                {"wl_id": waitlist_row.id, "now": now_utc}
            )

            # Get user details for message construction (optional but good practice)
            user_details = db.execute(
                text("SELECT full_name, phone FROM users WHERE id = :uid"),
                {"uid": waitlisted_user_id}
            ).fetchone()
            
            # Get service name
            service_details = db.execute(
                text("SELECT name FROM services WHERE id = :sid"),
                {"sid": appt_row.service_id}
            ).fetchone()
            
            msg_service_name = service_details.name if service_details else "Service"
            msg_text = f"A slot has opened up for {msg_service_name} at {appt_row.shop_id} on {appt_row.appointment_datetime}. Please book immediately."

            # Create Notification Record (SMS)
            db.execute(
                text("""
                    INSERT INTO notifications (user_id, appointment_id, waitlist_id, type, message, status, sent_at, created_at, updated_at)
                    VALUES (:uid, :aid, :wid, 'sms', :msg, 'sent', :now, :now, :now)
                """),
                {
                    "uid": waitlisted_user_id,
                    "aid": appointment_id,
                    "wid": waitlist_row.id,
                    "msg": msg_text,
                    "now": now_utc
                }
            )
            notification_sent = True
        
        db.commit()

        return CancelAppointmentResponse(
            appointment_id=appointment_id,
            new_status="vacated",
            refund_processed=True,
            notification_sent=notification_sent,
            waitlisted_user_id=waitlisted_user_id,
            refund_amount=refund_amount
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
