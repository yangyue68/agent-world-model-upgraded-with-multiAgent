import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class DeactivateRequest(BaseModel):
    reason: str
    audit_note: str


class DeactivateResponse(BaseModel):
    message: str
    station_id: int
    new_status: str


@app.put(
    "/stations/{station_id}/deactivate",
    response_model=DeactivateResponse,
    operation_id="deactivate_station",
    summary="Deactivate a station and record deactivation details",
    description="Update the status of a specified station to 'Inactive' and log the deactivation reason and audit note in the history table. The user performing this action must be authenticated.",
    tags=["PrimaryResource"],
)
def deactivate_station(
    station_id: int = Path(..., description="Internal ID of the station to deactivate"),
    body: DeactivateRequest = None,
):
    if body is None:
        raise HTTPException(status_code=422, detail="Body is required")

    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.connect() as conn:
        # Verify station exists and get its current status
        result = conn.execute(
            text("SELECT id, name, status FROM stations WHERE id = :sid"),
            {"sid": station_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Station not found")

        station_name = row[1]
        previous_status = row[2]

        # Check if already inactive
        if previous_status == "Inactive":
            raise HTTPException(
                status_code=400,
                detail=f"Station '{station_name}' is already Inactive",
            )

        # Update station status to Inactive
        conn.execute(
            text(
                "UPDATE stations SET status = 'Inactive', updated_at = CURRENT_TIMESTAMP WHERE id = :sid"
            ),
            {"sid": station_id},
        )

        # Insert into station_status_history
        conn.execute(
            text(
                """
                INSERT INTO station_status_history 
                (station_id, previous_status, new_status, reason, audit_note, changed_by_user_id)
                VALUES (:station_id, :prev_status, :new_status, :reason, :audit_note, :user_id)
                """
            ),
            {
                "station_id": station_id,
                "prev_status": previous_status,
                "new_status": "Inactive",
                "reason": body.reason,
                "audit_note": body.audit_note,
                "user_id": current_user_id,
            },
        )

        conn.commit()

    return DeactivateResponse(
        message=f"Station '{station_name}' has been deactivated successfully.",
        station_id=station_id,
        new_status="Inactive",
    )
