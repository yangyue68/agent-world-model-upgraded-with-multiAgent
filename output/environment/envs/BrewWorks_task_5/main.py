import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="BrewWorks Inventory API")


class CreateInventoryLotRequest(BaseModel):
    inventory_item_id: int
    lot_number: str
    quantity: float
    unit: str
    supplier_id: Optional[int] = None
    received_date: str
    storage_location_id: int
    unit_cost: Optional[float] = None
    alpha_acid: Optional[float] = None
    color_srm: Optional[float] = None


class InventoryLotResponse(BaseModel):
    id: int
    inventory_item_id: int
    lot_number: str
    quantity: float
    unit: str
    supplier_id: Optional[int] = None
    received_date: str
    storage_location_id: int
    unit_cost: Optional[float] = None
    alpha_acid: Optional[float] = None
    color_srm: Optional[float] = None
    created_by: int
    created_at: str
    updated_at: str


@app.post("/inventory-lots", response_model=InventoryLotResponse, operation_id="create_inventory_lot")
def create_inventory_lot(payload: CreateInventoryLotRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authenticated user context")

    # Validate inventory item exists
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM inventory_items WHERE id = :item_id"),
            {"item_id": payload.inventory_item_id}
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Inventory item not found")

        # Validate supplier if provided
        if payload.supplier_id is not None:
            result = conn.execute(
                text("SELECT id FROM suppliers WHERE id = :supplier_id"),
                {"supplier_id": payload.supplier_id}
            )
            if result.fetchone() is None:
                raise HTTPException(status_code=404, detail="Supplier not found")

        # Validate storage location exists
        result = conn.execute(
            text("SELECT id FROM storage_locations WHERE id = :location_id"),
            {"location_id": payload.storage_location_id}
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Storage location not found")

        # Check for duplicate lot number within the same inventory item
        result = conn.execute(
            text("""
                SELECT id FROM inventory_lots 
                WHERE inventory_item_id = :item_id AND lot_number = :lot_number
            """),
            {"item_id": payload.inventory_item_id, "lot_number": payload.lot_number}
        )
        if result.fetchone() is not None:
            raise HTTPException(status_code=409, detail="Lot number already exists for this inventory item")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        insert_stmt = text("""
            INSERT INTO inventory_lots (
                inventory_item_id,
                lot_number,
                quantity,
                unit,
                supplier_id,
                received_date,
                storage_location_id,
                unit_cost,
                alpha_acid,
                color_srm,
                created_by,
                created_at,
                updated_at
            ) VALUES (
                :inventory_item_id,
                :lot_number,
                :quantity,
                :unit,
                :supplier_id,
                :received_date,
                :storage_location_id,
                :unit_cost,
                :alpha_acid,
                :color_srm,
                :created_by,
                :created_at,
                :updated_at
            )
        """)

        params = {
            "inventory_item_id": payload.inventory_item_id,
            "lot_number": payload.lot_number,
            "quantity": payload.quantity,
            "unit": payload.unit,
            "supplier_id": payload.supplier_id,
            "received_date": payload.received_date,
            "storage_location_id": payload.storage_location_id,
            "unit_cost": payload.unit_cost,
            "alpha_acid": payload.alpha_acid,
            "color_srm": payload.color_srm,
            "created_by": current_user_id,
            "created_at": now,
            "updated_at": now,
        }

        conn.execute(insert_stmt, params)
        conn.commit()

        # Fetch the newly inserted row
        select_stmt = text("""
            SELECT 
                id,
                inventory_item_id,
                lot_number,
                quantity,
                unit,
                supplier_id,
                received_date,
                storage_location_id,
                unit_cost,
                alpha_acid,
                color_srm,
                created_by,
                created_at,
                updated_at
            FROM inventory_lots
            WHERE inventory_item_id = :item_id AND lot_number = :lot_number
        """)

        result = conn.execute(select_stmt, {"item_id": payload.inventory_item_id, "lot_number": payload.lot_number})
        row = result.fetchone()

        if row is None:
            raise HTTPException(status_code=500, detail="Failed to retrieve created inventory lot")

        return InventoryLotResponse(
            id=row.id,
            inventory_item_id=row.inventory_item_id,
            lot_number=row.lot_number,
            quantity=float(row.quantity),
            unit=row.unit,
            supplier_id=row.supplier_id,
            received_date=str(row.received_date),
            storage_location_id=row.storage_location_id,
            unit_cost=float(row.unit_cost) if row.unit_cost is not None else None,
            alpha_acid=float(row.alpha_acid) if row.alpha_acid is not None else None,
            color_srm=float(row.color_srm) if row.color_srm is not None else None,
            created_by=row.created_by,
            created_at=str(row.created_at),
            updated_at=str(row.updated_at),
        )
