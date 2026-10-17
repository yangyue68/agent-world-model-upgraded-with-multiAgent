import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AssetResponse(BaseModel):
    id: int
    asset_tag: str
    name: str
    status: str


class DecommissionResponse(BaseModel):
    workflow_id: int
    approval_request_id: int
    audit_log_id: int


@app.get("/assets/by-tag/{asset_tag}", response_model=AssetResponse, operation_id="get_asset_by_tag")
def get_asset_by_tag(asset_tag: str):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, asset_tag, name, status FROM assets WHERE asset_tag = :tag"),
            {"tag": asset_tag},
        ).fetchone()
        if result is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return AssetResponse(
            id=result[0],
            asset_tag=result[1],
            name=result[2],
            status=result[3],
        )


@app.post("/assets/{asset_id}/decommission", response_model=DecommissionResponse, operation_id="initiate_asset_decommission")
def initiate_asset_decommission(
    asset_id: int,
    reason_code: str = Query(...),
    assigned_to_user_id: int = Query(...),
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="No authenticated user")
    try:
        initiated_by_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.begin() as conn:
        # Verify asset exists
        asset_row = conn.execute(
            text("SELECT id, asset_tag, name, status FROM assets WHERE id = :aid"),
            {"aid": asset_id},
        ).fetchone()
        if asset_row is None:
            raise HTTPException(status_code=404, detail="Asset not found")

        # Verify reason code exists and is active
        reason_row = conn.execute(
            text("SELECT id, label FROM decommission_reasons WHERE code = :code AND is_active = 1"),
            {"code": reason_code},
        ).fetchone()
        if reason_row is None:
            raise HTTPException(status_code=400, detail="Invalid or inactive reason code")
        reason_id = reason_row[0]
        reason_label = reason_row[1]

        # Verify assigned user exists
        assigned_user_row = conn.execute(
            text("SELECT id, full_name FROM users WHERE id = :uid"),
            {"uid": assigned_to_user_id},
        ).fetchone()
        if assigned_user_row is None:
            raise HTTPException(status_code=400, detail="Assigned user not found")

        # Verify initiating user exists
        initiator_row = conn.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": initiated_by_user_id},
        ).fetchone()
        if initiator_row is None:
            raise HTTPException(status_code=400, detail="Initiating user not found")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Create approval request
        approval_result = conn.execute(
            text("""
                INSERT INTO approval_requests (asset_id, requested_by_user_id, assigned_to_user_id, reason_id, status, request_notes, created_at, updated_at)
                VALUES (:asset_id, :requested_by, :assigned_to, :reason_id, 'pending', :notes, :now, :now)
            """),
            {
                "asset_id": asset_id,
                "requested_by": initiated_by_user_id,
                "assigned_to": assigned_to_user_id,
                "reason_id": reason_id,
                "notes": f"Decommissioning requested for reason: {reason_label}",
                "now": now,
            },
        )
        approval_request_id = approval_result.lastrowid

        # Create decommission workflow
        workflow_result = conn.execute(
            text("""
                INSERT INTO decommission_workflows (asset_id, approval_request_id, initiated_by_user_id, reason_id, status, disposition_status, notes, initiated_at, created_at, updated_at)
                VALUES (:asset_id, :approval_id, :initiated_by, :reason_id, 'pending_approval', 'pending', :notes, :now, :now, :now)
            """),
            {
                "asset_id": asset_id,
                "approval_id": approval_request_id,
                "initiated_by": initiated_by_user_id,
                "reason_id": reason_id,
                "notes": f"Workflow initiated for {asset_row[1]} ({asset_row[2]})",
                "now": now,
            },
        )
        workflow_id = workflow_result.lastrowid

        # Update the workflow to link back to itself via approval_request_id already set above
        # Now create audit log entry
        audit_result = conn.execute(
            text("""
                INSERT INTO audit_logs (user_id, asset_id, workflow_id, approval_request_id, action, entity_type, entity_id, old_value, new_value, disposition_status, details, logged_at, created_at)
                VALUES (:user_id, :asset_id, :workflow_id, :approval_id, 'workflow_initiated', 'decommission_workflow', :entity_id, NULL, :new_value, 'pending', :details, :now, :now)
            """),
            {
                "user_id": initiated_by_user_id,
                "asset_id": asset_id,
                "workflow_id": workflow_id,
                "approval_id": approval_request_id,
                "entity_id": workflow_id,
                "new_value": '{"status":"pending_approval"}',
                "details": f"Decommission workflow initiated for asset {asset_row[1]} with reason '{reason_label}'. Approval assigned to user {assigned_to_user_id}.",
                "now": now,
            },
        )
        audit_log_id = audit_result.lastrowid

        return DecommissionResponse(
            workflow_id=workflow_id,
            approval_request_id=approval_request_id,
            audit_log_id=audit_log_id,
        )
