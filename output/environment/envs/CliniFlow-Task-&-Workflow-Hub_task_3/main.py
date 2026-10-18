import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CreateProtocolRequest(BaseModel):
    practice_id: int
    title: str
    trigger_event_type: str
    trigger_resource_type: str
    audit_logging_enabled: bool
    approval_note: str


class PublishProtocolRequest(BaseModel):
    approval_note: str


@app.post("/protocols", operation_id="create_vaccination_protocol")
def create_vaccination_protocol(req: CreateProtocolRequest):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Missing user context")
    try:
        user_id = int(current_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with engine.begin() as conn:
        # Verify practice exists
        result = conn.execute(
            text("SELECT id FROM practices WHERE id = :pid"),
            {"pid": req.practice_id},
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Practice not found")

        # Insert protocol
        result = conn.execute(
            text(
                """
                INSERT INTO protocols (
                    practice_id, title, status, trigger_event_type,
                    trigger_resource_type, audit_logging_enabled,
                    approval_note, created_by, published_at,
                    created_at, updated_at
                ) VALUES (
                    :practice_id, :title, 'draft', :trigger_event_type,
                    :trigger_resource_type, :audit_logging_enabled,
                    :approval_note, :created_by, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "practice_id": req.practice_id,
                "title": req.title,
                "trigger_event_type": req.trigger_event_type,
                "trigger_resource_type": req.trigger_resource_type,
                "audit_logging_enabled": 1 if req.audit_logging_enabled else 0,
                "approval_note": req.approval_note,
                "created_by": user_id,
            },
        )
        protocol_id = result.lastrowid

        # Insert task
        result = conn.execute(
            text(
                """
                INSERT INTO protocol_tasks (
                    protocol_id, title, description, assignee_role,
                    initial_status, created_at, updated_at
                ) VALUES (
                    :protocol_id, :title, :description, :assignee_role,
                    :initial_status, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "protocol_id": protocol_id,
                "title": "Verify VIS documentation completeness and attach to chart (assign to RN, status = Pending Review)",
                "description": "Verify VIS documentation completeness and attach to chart",
                "assignee_role": "RN",
                "initial_status": "Pending Review",
            },
        )
        task_id = result.lastrowid

        # Audit log for creation
        conn.execute(
            text(
                """
                INSERT INTO protocol_audit_log (
                    protocol_id, user_id, action, from_state, to_state,
                    details, created_at
                ) VALUES (
                    :protocol_id, :user_id, 'create', NULL, 'draft',
                    'Protocol created', CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "protocol_id": protocol_id,
                "user_id": user_id,
            },
        )

    return {"protocol_id": protocol_id, "task_id": task_id}


@app.put("/protocols/{protocol_id}/publish", operation_id="publish_protocol")
def publish_protocol(protocol_id: int, req: PublishProtocolRequest):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Missing user context")
    try:
        user_id = int(current_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with engine.begin() as conn:
        # Get current protocol state
        result = conn.execute(
            text("SELECT status FROM protocols WHERE id = :pid"),
            {"pid": protocol_id},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Protocol not found")

        current_status = row[0]
        if current_status != "draft":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot publish protocol in '{current_status}' state. Only draft protocols can be published.",
            )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Update protocol to published
        conn.execute(
            text(
                """
                UPDATE protocols
                SET status = 'published',
                    approval_note = :approval_note,
                    published_at = :published_at,
                    updated_at = :updated_at
                WHERE id = :pid
                """
            ),
            {
                "pid": protocol_id,
                "approval_note": req.approval_note,
                "published_at": now,
                "updated_at": now,
            },
        )

        # Audit log for publishing
        conn.execute(
            text(
                """
                INSERT INTO protocol_audit_log (
                    protocol_id, user_id, action, from_state, to_state,
                    details, created_at
                ) VALUES (
                    :protocol_id, :user_id, 'publish', 'draft', 'published',
                    :details, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "protocol_id": protocol_id,
                "user_id": user_id,
                "details": req.approval_note,
            },
        )

    return {
        "protocol_id": protocol_id,
        "status": "published",
        "published_at": now,
    }
