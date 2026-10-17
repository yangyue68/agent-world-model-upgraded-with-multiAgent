import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class GuestUpdateRequest(BaseModel):
    phone: str | None = None
    email: str | None = None


class GuestUpdateResponse(BaseModel):
    reservation_code: str
    guest_id: int
    updated_phone: str | None = None
    updated_email: str | None = None


@app.put("/reservations/{reservation_code}/guest", response_model=GuestUpdateResponse, operation_id="update_reservation_guest_info")
def update_reservation_guest_info(reservation_code: str, payload: GuestUpdateRequest):
    with engine.begin() as conn:
        # Find the reservation by code
        res_row = conn.execute(
            text("SELECT id FROM reservations WHERE reservation_code = :code"),
            {"code": reservation_code},
        ).fetchone()

        if res_row is None:
            raise HTTPException(status_code=404, detail="Reservation not found")

        reservation_id = res_row[0]

        # Find the guest associated with this reservation
        guest_row = conn.execute(
            text("SELECT id, phone, email FROM guests WHERE reservation_id = :rid LIMIT 1"),
            {"rid": reservation_id},
        ).fetchone()

        if guest_row is None:
            raise HTTPException(status_code=404, detail="Guest not found for this reservation")

        guest_id = guest_row[0]
        current_phone = guest_row[1]
        current_email = guest_row[2]

        new_phone = payload.phone if payload.phone is not None else current_phone
        new_email = payload.email if payload.email is not None else current_email

        # Update only if there are changes
        if new_phone != current_phone or new_email != current_email:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                text("""
                    UPDATE guests
                    SET phone = :phone,
                        email = :email,
                        updated_at = :updated_at
                    WHERE id = :gid
                """),
                {
                    "phone": new_phone,
                    "email": new_email,
                    "updated_at": now_str,
                    "gid": guest_id,
                },
            )

        return GuestUpdateResponse(
            reservation_code=reservation_code,
            guest_id=guest_id,
            updated_phone=new_phone,
            updated_email=new_email,
        )
