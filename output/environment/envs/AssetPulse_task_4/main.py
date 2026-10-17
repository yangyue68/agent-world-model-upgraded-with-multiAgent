import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="AssetPulse API")


class AssignRequest(BaseModel):
    location_id: int
    custodian_id: int
    assigned_by_user_id: int


class LookupAssetResponse(BaseModel):
    asset_id: int


class LookupLocationResponse(BaseModel):
    location_id: int


class LookupCustodianResponse(BaseModel):
    custodian_id: int


class AssignResponse(BaseModel):
    assignment_id: int
    message: str


@app.post("/assets/{asset_id}/assign", operation_id="assign_asset", response_model=AssignResponse)
def assign_asset(asset_id: int, body: AssignRequest):
    with engine.connect() as conn:
        # Verify asset exists and is active
        result = conn.execute(
            text("SELECT id, status FROM assets WHERE id = :aid"),
            {"aid": asset_id},
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Asset not found")
        if result.status != "active":
            raise HTTPException(status_code=400, detail="Asset is not active")

        # Verify location exists
        loc_result = conn.execute(
            text("SELECT id FROM locations WHERE id = :lid"),
            {"lid": body.location_id},
        ).fetchone()
        if not loc_result:
            raise HTTPException(status_code=404, detail="Location not found")

        # Verify custodian exists
        cust_result = conn.execute(
            text("SELECT id FROM custodians WHERE id = :cid"),
            {"cid": body.custodian_id},
        ).fetchone()
        if not cust_result:
            raise HTTPException(status_code=404, detail="Custodian not found")

        # Verify user exists
        user_result = conn.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": body.assigned_by_user_id},
        ).fetchone()
        if not user_result:
            raise HTTPException(status_code=404, detail="User not found")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # End any previous active assignment for this asset
        conn.execute(
            text(
                "UPDATE asset_assignments SET effective_to = :now, updated_at = :now "
                "WHERE asset_id = :aid AND effective_to IS NULL"
            ),
            {"now": now, "aid": asset_id},
        )

        # Insert new assignment
        insert_result = conn.execute(
            text(
                "INSERT INTO asset_assignments (asset_id, location_id, custodian_id, assigned_by_user_id, effective_from, created_at, updated_at) "
                "VALUES (:aid, :lid, :cid, :uid, :now, :now, :now)"
            ),
            {
                "aid": asset_id,
                "lid": body.location_id,
                "cid": body.custodian_id,
                "uid": body.assigned_by_user_id,
                "now": now,
            },
        )
        assignment_id = insert_result.lastrowid
        conn.commit()

    return AssignResponse(
        assignment_id=assignment_id,
        message=f"Asset {asset_id} assigned successfully.",
    )


@app.get("/assets/lookup", operation_id="lookup_asset_by_serial", response_model=LookupAssetResponse)
def lookup_asset_by_serial(serial_number: str = Query(...)):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, status FROM assets WHERE serial_number = :sn"),
            {"sn": serial_number},
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Asset not found")
        if result.status != "active":
            raise HTTPException(status_code=400, detail="Asset is not active")
    return LookupAssetResponse(asset_id=result.id)


@app.get("/locations/lookup", operation_id="lookup_location_by_name", response_model=LookupLocationResponse)
def lookup_location_by_name(name: str = Query(...)):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM locations WHERE name = :name"),
            {"name": name},
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Location not found")
    return LookupLocationResponse(location_id=result.id)


@app.get("/custodians/lookup", operation_id="lookup_custodian_by_name", response_model=LookupCustodianResponse)
def lookup_custodian_by_name(name: str = Query(...)):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM custodians WHERE name = :name"),
            {"name": name},
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Custodian not found")
    return LookupCustodianResponse(custodian_id=result.id)
