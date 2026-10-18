import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class SessionSlotResponse(BaseModel):
    id: int
    tutor_id: int
    subject_id: int | None
    start_time: str
    end_time: str
    duration_minutes: int
    status: str
    is_published: int


class MarkUnavailableResponse(BaseModel):
    success: bool
    message: str


@app.get("/session-slots/{slot_id}", operation_id="get_session_slot", response_model=SessionSlotResponse)
def get_session_slot(slot_id: int = Path(...)):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, tutor_id, subject_id, start_time, end_time, duration_minutes, status, is_published "
                "FROM session_slots WHERE id = :slot_id"
            ),
            {"slot_id": slot_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Session slot not found")
        return SessionSlotResponse(
            id=row[0],
            tutor_id=row[1],
            subject_id=row[2],
            start_time=str(row[3]),
            end_time=str(row[4]),
            duration_minutes=row[5],
            status=row[6],
            is_published=row[7],
        )


@app.put("/session-slots/{slot_id}/unavailable", operation_id="mark_slot_unavailable", response_model=MarkUnavailableResponse)
def mark_slot_unavailable(slot_id: int = Path(...)):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if current_user_id_str is None:
        raise HTTPException(status_code=401, detail="Missing user context")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with engine.begin() as conn:
        # Verify the slot exists and belongs to the current user's tutor profile
        slot_row = conn.execute(
            text(
                "SELECT ss.id, ss.tutor_id, ss.status, ss.is_published, t.user_id "
                "FROM session_slots ss JOIN tutors t ON ss.tutor_id = t.id "
                "WHERE ss.id = :slot_id"
            ),
            {"slot_id": slot_id},
        ).fetchone()

        if slot_row is None:
            raise HTTPException(status_code=404, detail="Session slot not found")

        _, tutor_id, status, is_published, owner_user_id = slot_row

        if owner_user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Not authorized to modify this slot")

        if is_published != 1:
            raise HTTPException(status_code=400, detail="Slot must be published to mark unavailable")

        if status != "available":
            raise HTTPException(status_code=400, detail=f"Slot status is '{status}', expected 'available'")

        # Update the slot status to unavailable
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            text(
                "UPDATE session_slots SET status = 'unavailable', updated_at = :now WHERE id = :slot_id"
            ),
            {"slot_id": slot_id, "now": now},
        )

        # Notify students on waitlist for this slot that it is no longer available
        waitlist_rows = conn.execute(
            text("SELECT student_id FROM waitlist WHERE slot_id = :slot_id AND status = 'waiting'"),
            {"slot_id": slot_id},
        ).fetchall()

        for (student_id,) in waitlist_rows:
            payload = f'{{"message": "The session slot you were waitlisted for has been marked unavailable.", "slot_id": {slot_id}}}'
            conn.execute(
                text(
                    "INSERT INTO notifications (user_id, type, payload, is_read, created_at) "
                    "VALUES (:uid, :type, :payload, 0, :now)"
                ),
                {"uid": student_id, "type": "waitlist_expired", "payload": payload, "now": now},
            )
            conn.execute(
                text(
                    "UPDATE waitlist SET status = 'expired', updated_at = :now "
                    "WHERE slot_id = :slot_id AND student_id = :sid AND status = 'waiting'"
                ),
                {"slot_id": slot_id, "sid": student_id, "now": now},
            )

    return MarkUnavailableResponse(success=True, message="Slot marked unavailable and waitlist notified")
