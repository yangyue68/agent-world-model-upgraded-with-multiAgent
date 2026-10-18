import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RecertificationRequirementsUpdate(BaseModel):
    max_online_ceus: Optional[int] = None
    min_live_ceus: Optional[int] = None
    auto_renewal_enabled: Optional[bool] = None


class RecertificationRequirementsResponse(BaseModel):
    id: int
    certification_id: int
    max_online_ceus: int
    min_live_ceus: int
    auto_renewal_enabled: bool
    created_at: str
    updated_at: str


@app.put(
    "/certifications/{certification_id}/recertification-requirements",
    response_model=RecertificationRequirementsResponse,
    operation_id="update_recertification_requirements",
    tags=["PrimaryResource"],
)
def update_recertification_requirements(
    certification_id: int = Path(..., description="Internal ID of the certification"),
    body: RecertificationRequirementsUpdate = ...,
):
    # Verify certification exists
    with engine.connect() as conn:
        cert_result = conn.execute(
            text("SELECT id FROM certifications WHERE id = :cid"),
            {"cid": certification_id},
        ).fetchone()
        if cert_result is None:
            raise HTTPException(status_code=404, detail="Certification not found")

        # Check if recertification requirements exist for this certification
        req_result = conn.execute(
            text(
                "SELECT id, certification_id, max_online_ceus, min_live_ceus, auto_renewal_enabled, created_at, updated_at "
                "FROM recertification_requirements WHERE certification_id = :cid"
            ),
            {"cid": certification_id},
        ).fetchone()

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if req_result is None:
            # Insert new record
            max_online = body.max_online_ceus if body.max_online_ceus is not None else 0
            min_live = body.min_live_ceus if body.min_live_ceus is not None else 0
            auto_renewal = 1 if body.auto_renewal_enabled else 0

            insert_result = conn.execute(
                text(
                    "INSERT INTO recertification_requirements "
                    "(certification_id, max_online_ceus, min_live_ceus, auto_renewal_enabled, created_at, updated_at) "
                    "VALUES (:cid, :max_online, :min_live, :auto_renewal, :created_at, :updated_at)"
                ),
                {
                    "cid": certification_id,
                    "max_online": max_online,
                    "min_live": min_live,
                    "auto_renewal": auto_renewal,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            conn.commit()

            new_id = insert_result.lastrowid
            return RecertificationRequirementsResponse(
                id=new_id,
                certification_id=certification_id,
                max_online_ceus=max_online,
                min_live_ceus=min_live,
                auto_renewal_enabled=bool(auto_renewal),
                created_at=now,
                updated_at=now,
            )
        else:
            # Update existing record
            current_max_online = req_result[2]
            current_min_live = req_result[3]
            current_auto_renewal = req_result[4]
            created_at = req_result[5]

            new_max_online = (
                body.max_online_ceus
                if body.max_online_ceus is not None
                else current_max_online
            )
            new_min_live = (
                body.min_live_ceus if body.min_live_ceus is not None else current_min_live
            )
            new_auto_renewal = (
                1 if body.auto_renewal_enabled else 0
                if body.auto_renewal_enabled is not None
                else current_auto_renewal
            )

            conn.execute(
                text(
                    "UPDATE recertification_requirements "
                    "SET max_online_ceus = :max_online, min_live_ceus = :min_live, "
                    "auto_renewal_enabled = :auto_renewal, updated_at = :updated_at "
                    "WHERE certification_id = :cid"
                ),
                {
                    "max_online": new_max_online,
                    "min_live": new_min_live,
                    "auto_renewal": new_auto_renewal,
                    "updated_at": now,
                    "cid": certification_id,
                },
            )
            conn.commit()

            return RecertificationRequirementsResponse(
                id=req_result[0],
                certification_id=certification_id,
                max_online_ceus=new_max_online,
                min_live_ceus=new_min_live,
                auto_renewal_enabled=bool(new_auto_renewal),
                created_at=str(created_at),
                updated_at=now,
            )
