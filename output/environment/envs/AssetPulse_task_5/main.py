import os
from typing import List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class BulkReassignResponse(BaseModel):
    reassigned_count: int
    affected_asset_ids: List[int]


@app.post(
    "/assets/bulk-reassign",
    operation_id="bulk_reassign_assets",
    response_model=BulkReassignResponse,
    tags=["PrimaryResource"],
)
def bulk_reassign_assets(
    current_location_name: str = Query(..., description="Name of the current location to filter assets"),
    current_custodian_name: str = Query(..., description="Name of the current custodian to filter assets"),
    new_location_name: str = Query(..., description="Name of the new location to assign assets to"),
    new_custodian_name: str = Query(..., description="Name of the new custodian to assign assets to"),
):
    with engine.connect() as conn:
        # Resolve current location id
        row = conn.execute(
            text("SELECT id FROM locations WHERE name = :name"),
            {"name": current_location_name},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Current location '{current_location_name}' not found")
        current_location_id = row[0]

        # Resolve current custodian id
        row = conn.execute(
            text("SELECT id FROM custodians WHERE name = :name"),
            {"name": current_custodian_name},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Current custodian '{current_custodian_name}' not found")
        current_custodian_id = row[0]

        # Resolve new location id
        row = conn.execute(
            text("SELECT id FROM locations WHERE name = :name"),
            {"name": new_location_name},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"New location '{new_location_name}' not found")
        new_location_id = row[0]

        # Resolve new custodian id
        row = conn.execute(
            text("SELECT id FROM custodians WHERE name = :name"),
            {"name": new_custodian_name},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"New custodian '{new_custodian_name}' not found")
        new_custodian_id = row[0]

        # Find all assets matching current location and custodian
        asset_rows = conn.execute(
            text(
                "SELECT id FROM assets WHERE location_id = :loc_id AND custodian_id = :cust_id ORDER BY id"
            ),
            {"loc_id": current_location_id, "cust_id": current_custodian_id},
        ).fetchall()

        affected_asset_ids = [r[0] for r in asset_rows]
        reassigned_count = len(affected_asset_ids)

        if reassigned_count == 0:
            return BulkReassignResponse(reassigned_count=0, affected_asset_ids=[])

        # Get actor user id from environment
        actor_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not actor_user_id_str:
            raise HTTPException(status_code=500, detail="Actor user ID not available")
        try:
            actor_user_id = int(actor_user_id_str)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid actor user ID")

        # Verify actor exists
        user_row = conn.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": actor_user_id},
        ).fetchone()
        if not user_row:
            raise HTTPException(status_code=401, detail="Actor user not found")

        # Update assets and insert history records within a transaction
        for asset_id in affected_asset_ids:
            # Insert into assignment history
            conn.execute(
                text(
                    """INSERT INTO asset_assignment_history 
                       (asset_id, previous_location_id, previous_custodian_id, new_location_id, new_custodian_id, changed_by_user_id)
                       VALUES (:asset_id, :prev_loc, :prev_cust, :new_loc, :new_cust, :changed_by)"""
                ),
                {
                    "asset_id": asset_id,
                    "prev_loc": current_location_id,
                    "prev_cust": current_custodian_id,
                    "new_loc": new_location_id,
                    "new_cust": new_custodian_id,
                    "changed_by": actor_user_id,
                },
            )

            # Update the asset
            conn.execute(
                text(
                    """UPDATE assets 
                       SET location_id = :new_loc, custodian_id = :new_cust, updated_at = CURRENT_TIMESTAMP
                       WHERE id = :asset_id"""
                ),
                {
                    "new_loc": new_location_id,
                    "new_cust": new_custodian_id,
                    "asset_id": asset_id,
                },
            )

        conn.commit()

        return BulkReassignResponse(reassigned_count=reassigned_count, affected_asset_ids=affected_asset_ids)
