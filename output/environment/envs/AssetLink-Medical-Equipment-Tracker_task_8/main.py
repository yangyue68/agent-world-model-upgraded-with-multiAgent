import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class TicketItem(BaseModel):
    ticket_number: str = Field(..., description="Maintenance ticket number to close")
    equipment_asset_tag: str = Field(..., description="Equipment asset tag associated with the ticket")
    calibration_event_id: str = Field(..., description="Completed calibration event ID to link")


class BulkCloseRequest(BaseModel):
    tickets: list[TicketItem] = Field(..., description="List of ticket details to process")
    completion_reason: str = Field(..., description="Reason for completing the tickets late")


class ProcessedTicketResponse(BaseModel):
    ticket_number: str
    status: str
    equipment_asset_tag: str
    calibration_event_id: str


class BulkCloseResponse(BaseModel):
    success_count: int
    processed_tickets: list[ProcessedTicketResponse]


@app.post(
    "/bulk-close-maintenance-tickets",
    response_model=BulkCloseResponse,
    operation_id="bulk_close_maintenance_tickets",
)
def bulk_close_maintenance_tickets(request: BulkCloseRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    processed_tickets = []
    success_count = 0

    with engine.begin() as conn:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        for item in request.tickets:
            # Validate ticket exists and is overdue/preventive
            ticket_row = conn.execute(
                text("""
                    SELECT mt.id, mt.ticket_number, mt.equipment_id, mt.status, mt.ticket_type, e.asset_tag
                    FROM maintenance_tickets mt
                    JOIN equipment e ON mt.equipment_id = e.id
                    WHERE mt.ticket_number = :ticket_number
                """),
                {"ticket_number": item.ticket_number},
            ).fetchone()

            if not ticket_row:
                raise HTTPException(status_code=404, detail=f"Ticket {item.ticket_number} not found")

            ticket_id, db_ticket_number, equipment_id, db_status, ticket_type, db_asset_tag = ticket_row

            # Check if ticket is already completed or cancelled
            if db_status in ("completed", "completed_late", "cancelled"):
                raise HTTPException(status_code=400, detail=f"Ticket {item.ticket_number} is already closed")

            # Validate equipment asset tag matches
            if db_asset_tag != item.equipment_asset_tag:
                raise HTTPException(
                    status_code=400,
                    detail=f"Asset tag mismatch for ticket {item.ticket_number}: expected {db_asset_tag}, got {item.equipment_asset_tag}",
                )

            # Validate calibration event exists and is completed
            cal_row = conn.execute(
                text("""
                    SELECT ce.id, ce.event_number, ce.status, ce.maintenance_ticket_id
                    FROM calibration_events ce
                    WHERE ce.event_number = :calibration_event_id
                """),
                {"calibration_event_id": item.calibration_event_id},
            ).fetchone()

            if not cal_row:
                raise HTTPException(status_code=404, detail=f"Calibration event {item.calibration_event_id} not found")

            cal_id, cal_event_number, cal_status, cal_ticket_id = cal_row

            if cal_status != "completed":
                raise HTTPException(
                    status_code=400,
                    detail=f"Calibration event {item.calibration_event_id} is not completed (status: {cal_status})",
                )

            # Update maintenance ticket
            conn.execute(
                text("""
                    UPDATE maintenance_tickets
                    SET status = 'completed_late',
                        completed_at = :completed_at,
                        completion_reason = :completion_reason,
                        updated_at = :updated_at
                    WHERE id = :ticket_id
                """),
                {
                    "completed_at": now,
                    "completion_reason": request.completion_reason,
                    "updated_at": now,
                    "ticket_id": ticket_id,
                },
            )

            # Link calibration event to ticket if not already linked
            if cal_ticket_id != ticket_id:
                conn.execute(
                    text("""
                        UPDATE calibration_events
                        SET maintenance_ticket_id = :ticket_id,
                            updated_at = :updated_at
                        WHERE id = :cal_id
                    """),
                    {
                        "ticket_id": ticket_id,
                        "updated_at": now,
                        "cal_id": cal_id,
                    },
                )

            # Insert audit log entry
            conn.execute(
                text("""
                    INSERT INTO audit_log (user_id, action, entity_type, entity_id, details, created_at)
                    VALUES (:user_id, :action, :entity_type, :entity_id, :details, :created_at)
                """),
                {
                    "user_id": current_user_id,
                    "action": "bulk_closed",
                    "entity_type": "maintenance_ticket",
                    "entity_id": ticket_id,
                    "details": f"Bulk closed ticket {item.ticket_number} as Completed Late. Linked to calibration {item.calibration_event_id}. Reason: {request.completion_reason}",
                    "created_at": now,
                },
            )

            processed_tickets.append(
                ProcessedTicketResponse(
                    ticket_number=item.ticket_number,
                    status="completed_late",
                    equipment_asset_tag=item.equipment_asset_tag,
                    calibration_event_id=item.calibration_event_id,
                )
            )
            success_count += 1

    return BulkCloseResponse(success_count=success_count, processed_tickets=processed_tickets)
