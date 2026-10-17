import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RevokeAssignmentRequest(BaseModel):
    reason: str


class CancelAccessRequestPayload(BaseModel):
    reason: str


@app.put(
    "/contractors/{contractor_id}/assignments/{assignment_id}/revoke",
    operation_id="revoke_contractor_assignment"
)
def revoke_contractor_assignment(
    contractor_id: int,
    assignment_id: int,
    payload: RevokeAssignmentRequest,
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context.")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authenticated user context.")

    with engine.begin() as conn:
        # Verify contractor exists
        row = conn.execute(
            text("SELECT id FROM contractors WHERE id = :cid"),
            {"cid": contractor_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Contractor not found.")

        # Fetch assignment and validate ownership/status
        assignment_row = conn.execute(
            text("""
                SELECT ca.id, ca.contractor_id, ca.access_group_id, ca.status, ag.name AS group_name, s.name AS site_name
                FROM contractor_assignments ca
                JOIN access_groups ag ON ag.id = ca.access_group_id
                JOIN sites s ON s.id = ag.site_id
                WHERE ca.id = :aid AND ca.contractor_id = :cid
            """),
            {"aid": assignment_id, "cid": contractor_id},
        ).fetchone()

        if assignment_row is None:
            raise HTTPException(status_code=404, detail="Assignment not found for this contractor.")

        if assignment_row.status != "active":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot revoke assignment in '{assignment_row.status}' state.",
            )

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Update assignment to revoked
        result = conn.execute(
            text("""
                UPDATE contractor_assignments
                SET status = 'revoked',
                    revoked_at = :now,
                    revoked_by_user_id = :uid,
                    revocation_reason = :reason,
                    updated_at = :now
                WHERE id = :aid AND status = 'active'
            """),
            {
                "now": now_utc,
                "uid": current_user_id,
                "reason": payload.reason,
                "aid": assignment_id,
            },
        )

        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="Concurrent modification detected; assignment no longer active.")

        # Insert audit log entry
        details_msg = (
            f"Revoked Daniel Ortiz's assignment to '{assignment_row.group_name}' "
            f"at {assignment_row.site_name} - Reason: {payload.reason}"
        )
        conn.execute(
            text("""
                INSERT INTO audit_logs (user_id, action, entity_type, entity_id, details, created_at)
                VALUES (:uid, 'revoke_assignment', 'contractor_assignment', :eid, :details, :now)
            """),
            {
                "uid": current_user_id,
                "eid": assignment_id,
                "details": details_msg,
                "now": now_utc,
            },
        )

    return {
        "success": True,
        "message": f"Successfully revoked assignment {assignment_id} for contractor {contractor_id}.",
    }


@app.put(
    "/contractors/{contractor_id}/access_requests/{request_id}/cancel",
    operation_id="cancel_access_request"
)
def cancel_access_request(
    contractor_id: int,
    request_id: int,
    payload: CancelAccessRequestPayload,
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context.")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authenticated user context.")

    with engine.begin() as conn:
        # Verify contractor exists
        row = conn.execute(
            text("SELECT id FROM contractors WHERE id = :cid"),
            {"cid": contractor_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Contractor not found.")

        # Fetch access request and validate ownership/status
        req_row = conn.execute(
            text("""
                SELECT ar.id, ar.contractor_id, ar.site_id, ar.access_group_id, ar.status,
                       ag.name AS group_name, s.name AS site_name
                FROM access_requests ar
                LEFT JOIN access_groups ag ON ag.id = ar.access_group_id
                JOIN sites s ON s.id = ar.site_id
                WHERE ar.id = :rid AND ar.contractor_id = :cid
            """),
            {"rid": request_id, "cid": contractor_id},
        ).fetchone()

        if req_row is None:
            raise HTTPException(status_code=404, detail="Access request not found for this contractor.")

        if req_row.status != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot cancel access request in '{req_row.status}' state.",
            )

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Update access request to cancelled
        result = conn.execute(
            text("""
                UPDATE access_requests
                SET status = 'cancelled',
                    resolved_at = :now,
                    resolved_by_user_id = :uid,
                    cancellation_reason = :reason,
                    updated_at = :now
                WHERE id = :rid AND status = 'pending'
            """),
            {
                "now": now_utc,
                "uid": current_user_id,
                "reason": payload.reason,
                "rid": request_id,
            },
        )

        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="Concurrent modification detected; request no longer pending.")

        # Build descriptive message for audit log
        location_desc = req_row.site_name
        if req_row.group_name:
            location_desc += f" ({req_row.group_name})"

        details_msg = (
            f"Cancelled pending access request for Daniel Ortiz at {location_desc} - Reason: {payload.reason}"
        )

        # Insert audit log entry
        conn.execute(
            text("""
                INSERT INTO audit_logs (user_id, action, entity_type, entity_id, details, created_at)
                VALUES (:uid, 'cancel_access_request', 'access_request', :eid, :details, :now)
            """),
            {
                "uid": current_user_id,
                "eid": request_id,
                "details": details_msg,
                "now": now_utc,
            },
        )

    return {
        "success": True,
        "message": f"Successfully cancelled access request {request_id} for contractor {contractor_id}.",
    }
