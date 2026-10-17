import os
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UpdateStatusRequest(BaseModel):
    status: Literal["Resolved"] = Field(..., description="New status for the service ticket (must be 'Resolved')")


class AddNoteRequest(BaseModel):
    note: str = Field(..., description="Content of the repair note")


class RecordLaborRequest(BaseModel):
    hours: float = Field(..., gt=0, description="Number of hours of labor time to record")


class TicketStatusResponse(BaseModel):
    id: int
    ticket_number: str
    status: str


class NoteResponse(BaseModel):
    id: int
    service_ticket_id: int
    note: str


class LaborResponse(BaseModel):
    id: int
    service_ticket_id: int
    hours: float


@app.put(
    "/service-tickets/{ticket_id}/status",
    response_model=TicketStatusResponse,
    operation_id="update_service_ticket_status",
    summary="Update the status of a service ticket",
    tags=["PrimaryResource"],
)
def update_service_ticket_status(
    ticket_id: int = Path(..., description="Internal ID of the service ticket"),
    body: UpdateStatusRequest = Body(...),
):
    with engine.connect() as conn:
        # Verify ticket exists and get current info
        result = conn.execute(
            text("SELECT id, ticket_number, status FROM service_tickets WHERE id = :tid"),
            {"tid": ticket_id},
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Service ticket not found")

        current_status = result.status
        new_status = body.status

        # Validate transition: only allow In Progress -> Resolved per task context, but generally validate against allowed statuses
        if new_status not in ("Open", "In Progress", "Resolved", "Closed"):
            raise HTTPException(status_code=400, detail="Invalid status value")

        # Update the status
        conn.execute(
            text(
                "UPDATE service_tickets SET status = :status, updated_at = CURRENT_TIMESTAMP WHERE id = :tid"
            ),
            {"status": new_status, "tid": ticket_id},
        )
        conn.commit()

        # Fetch updated record
        updated = conn.execute(
            text("SELECT id, ticket_number, status FROM service_tickets WHERE id = :tid"),
            {"tid": ticket_id},
        ).fetchone()

        return TicketStatusResponse(
            id=updated.id,
            ticket_number=updated.ticket_number,
            status=updated.status,
        )


@app.post(
    "/service-tickets/{ticket_id}/notes",
    response_model=NoteResponse,
    operation_id="add_service_note",
    summary="Add a repair note to a service ticket",
    tags=["PrimaryResource"],
)
def add_service_note(
    ticket_id: int = Path(..., description="Internal ID of the service ticket"),
    body: AddNoteRequest = Body(...),
):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=500, detail="Current user not identified")
    try:
        created_by = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID")

    with engine.connect() as conn:
        # Verify ticket exists
        ticket_exists = conn.execute(
            text("SELECT id FROM service_tickets WHERE id = :tid"),
            {"tid": ticket_id},
        ).fetchone()
        if not ticket_exists:
            raise HTTPException(status_code=404, detail="Service ticket not found")

        # Verify user exists
        user_exists = conn.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": created_by},
        ).fetchone()
        if not user_exists:
            raise HTTPException(status_code=400, detail="User not found")

        # Insert note
        result = conn.execute(
            text(
                "INSERT INTO service_notes (service_ticket_id, note, created_by) VALUES (:stid, :note, :created_by)"
            ),
            {"stid": ticket_id, "note": body.note, "created_by": created_by},
        )
        conn.commit()

        new_note_id = result.lastrowid

        # Fetch inserted record
        inserted = conn.execute(
            text("SELECT id, service_ticket_id, note FROM service_notes WHERE id = :nid"),
            {"nid": new_note_id},
        ).fetchone()

        return NoteResponse(
            id=inserted.id,
            service_ticket_id=inserted.service_ticket_id,
            note=inserted.note,
        )


@app.post(
    "/service-tickets/{ticket_id}/labor",
    response_model=LaborResponse,
    operation_id="record_labor_time",
    summary="Record labor time for a service ticket",
    tags=["PrimaryResource"],
)
def record_labor_time(
    ticket_id: int = Path(..., description="Internal ID of the service ticket"),
    body: RecordLaborRequest = Body(...),
):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=500, detail="Current user not identified")
    try:
        created_by = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID")

    with engine.connect() as conn:
        # Verify ticket exists
        ticket_exists = conn.execute(
            text("SELECT id FROM service_tickets WHERE id = :tid"),
            {"tid": ticket_id},
        ).fetchone()
        if not ticket_exists:
            raise HTTPException(status_code=404, detail="Service ticket not found")

        # Verify user exists
        user_exists = conn.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": created_by},
        ).fetchone()
        if not user_exists:
            raise HTTPException(status_code=400, detail="User not found")

        # Validate hours > 0 (already done by Pydantic, but double-check for DB constraint)
        if body.hours <= 0:
            raise HTTPException(status_code=400, detail="Hours must be greater than 0")

        # Insert labor entry
        result = conn.execute(
            text(
                "INSERT INTO labor_entries (service_ticket_id, hours, created_by) VALUES (:stid, :hours, :created_by)"
            ),
            {"stid": ticket_id, "hours": body.hours, "created_by": created_by},
        )
        conn.commit()

        new_labor_id = result.lastrowid

        # Fetch inserted record
        inserted = conn.execute(
            text("SELECT id, service_ticket_id, hours FROM labor_entries WHERE id = :lid"),
            {"lid": new_labor_id},
        ).fetchone()

        return LaborResponse(
            id=inserted.id,
            service_ticket_id=inserted.service_ticket_id,
            hours=float(inserted.hours),
        )
