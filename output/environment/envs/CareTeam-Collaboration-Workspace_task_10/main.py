import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CareTeam Collaboration Workspace API")


class RevokeConsentResponse(BaseModel):
    success: bool
    message: str
    consent_id: int
    patient_id: str
    organization_id: int


class ExportTaskItem(BaseModel):
    id: int
    status: str
    requested_at: str


@app.post(
    "/patients/{patient_id}/consents/{consent_id}/revoke",
    response_model=RevokeConsentResponse,
    operation_id="revoke_consent",
    tags=["PrimaryResource"],
)
def revoke_consent(patient_id: str, consent_id: int):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Current user not authenticated")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")

    with engine.begin() as conn:
        # Verify patient exists and get internal ID
        row = conn.execute(
            text("SELECT id FROM patients WHERE patient_identifier = :pid"),
            {"pid": patient_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
        db_patient_id = row[0]

        # Get consent record
        consent_row = conn.execute(
            text("""
                SELECT c.id, c.patient_id, c.organization_id, c.status
                FROM consents c
                WHERE c.id = :cid AND c.patient_id = :pid
            """),
            {"cid": consent_id, "pid": db_patient_id},
        ).fetchone()
        if not consent_row:
            raise HTTPException(status_code=404, detail="Consent not found for this patient")

        _, cp_patient_id, org_id, status = consent_row

        if status != "active":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot revoke consent with status '{status}'. Only active consents can be revoked.",
            )

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Update consent to revoked
        conn.execute(
            text("""
                UPDATE consents
                SET status = 'revoked',
                    revoked_at = :now,
                    revoked_by = :uid,
                    revocation_reason = 'Revoked via API request',
                    updated_at = :now
                WHERE id = :cid
            """),
            {"now": now_utc, "uid": current_user_id, "cid": consent_id},
        )

        # Cancel all pending FHIR export tasks for this consent
        conn.execute(
            text("""
                UPDATE fhir_export_tasks
                SET status = 'canceled',
                    canceled_at = :now,
                    canceled_by = :uid,
                    updated_at = :now
                WHERE consent_id = :cid AND status IN ('pending', 'in_progress')
            """),
            {"now": now_utc, "uid": current_user_id, "cid": consent_id},
        )

        # Insert audit log entry
        conn.execute(
            text("""
                INSERT INTO audit_logs (user_id, action, entity_type, entity_id, details, created_at)
                VALUES (:uid, 'consent_revoked', 'consent', :cid, :details, :now)
            """),
            {
                "uid": current_user_id,
                "cid": consent_id,
                "details": f"Consent {consent_id} revoked for patient {patient_id}",
                "now": now_utc,
            },
        )

    return RevokeConsentResponse(
        success=True,
        message="Consent successfully revoked and pending export tasks canceled",
        consent_id=consent_id,
        patient_id=patient_id,
        organization_id=org_id,
    )


@app.get(
    "/patients/{patient_id}/consents/{consent_id}/export-tasks",
    response_model=list[ExportTaskItem],
    operation_id="get_pending_export_tasks",
    tags=["PrimaryResource"],
)
def get_pending_export_tasks(patient_id: str, consent_id: int):
    with engine.connect() as conn:
        # Verify patient exists
        row = conn.execute(
            text("SELECT id FROM patients WHERE patient_identifier = :pid"),
            {"pid": patient_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
        db_patient_id = row[0]

        # Verify consent belongs to this patient
        consent_exists = conn.execute(
            text("SELECT 1 FROM consents WHERE id = :cid AND patient_id = :pid"),
            {"cid": consent_id, "pid": db_patient_id},
        ).fetchone()
        if not consent_exists:
            raise HTTPException(status_code=404, detail="Consent not found for this patient")

        rows = conn.execute(
            text("""
                SELECT id, status, requested_at
                FROM fhir_export_tasks
                WHERE consent_id = :cid
                ORDER BY requested_at ASC
            """),
            {"cid": consent_id},
        ).fetchall()

    result = []
    for r in rows:
        tid, tstatus, trequested = r
        # Normalize timestamp format
        req_str = str(trequested) if trequested else ""
        result.append(ExportTaskItem(id=tid, status=tstatus, requested_at=req_str))

    return result
