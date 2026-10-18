import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UpdateInventoryRequest(BaseModel):
    quantity_change: int
    lot_number: str
    expiration_date: str


class InventoryResponse(BaseModel):
    id: int
    medication_id: int
    quantity: int
    lot_number: str
    expiration_date: str
    updated_at: str


class MedicationItem(BaseModel):
    id: int
    name: str


@app.put("/inventory/{inventory_id}", response_model=InventoryResponse, operation_id="update_inventory_shipment")
def update_inventory_shipment(inventory_id: int, payload: UpdateInventoryRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.begin() as conn:
        # Verify user exists and has correct role (pharmacist or administrator)
        user_row = conn.execute(
            text("SELECT role FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).fetchone()
        
        if not user_row:
            raise HTTPException(status_code=403, detail="Forbidden")
            
        role = user_row[0]
        if role not in ("pharmacist", "administrator"):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Get current inventory state
        inv_row = conn.execute(
            text("""
                SELECT id, medication_id, quantity 
                FROM inventory 
                WHERE id = :inv_id
            """),
            {"inv_id": inventory_id}
        ).fetchone()

        if not inv_row:
            raise HTTPException(status_code=404, detail="Inventory record not found")

        med_id = inv_row[1]
        prev_qty = inv_row[2]
        
        new_qty = prev_qty + payload.quantity_change
        
        if new_qty < 0:
            raise HTTPException(status_code=400, detail="Quantity cannot be negative")

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Update inventory
        conn.execute(
            text("""
                UPDATE inventory 
                SET quantity = :qty, 
                    lot_number = :lot, 
                    expiration_date = :exp, 
                    updated_at = :upd
                WHERE id = :inv_id
            """),
            {
                "qty": new_qty,
                "lot": payload.lot_number,
                "exp": payload.expiration_date,
                "upd": now_iso,
                "inv_id": inventory_id
            }
        )

        # Insert transaction log
        conn.execute(
            text("""
                INSERT INTO inventory_transactions (
                    inventory_id, user_id, transaction_type, 
                    quantity_change, previous_quantity, new_quantity, 
                    lot_number, expiration_date, notes, created_at
                ) VALUES (
                    :inv_id, :uid, 'shipment',
                    :q_chg, :prev_q, :new_q,
                    :lot, :exp, :notes, :created
                )
            """),
            {
                "inv_id": inventory_id,
                "uid": user_id,
                "q_chg": payload.quantity_change,
                "prev_q": prev_qty,
                "new_q": new_qty,
                "lot": payload.lot_number,
                "exp": payload.expiration_date,
                "notes": f"Shipment update via API for medication ID {med_id}",
                "created": now_iso
            }
        )

        # Fetch final state to return
        final_row = conn.execute(
            text("""
                SELECT id, medication_id, quantity, lot_number, expiration_date, updated_at
                FROM inventory
                WHERE id = :inv_id
            """),
            {"inv_id": inventory_id}
        ).fetchone()

        return InventoryResponse(
            id=final_row[0],
            medication_id=final_row[1],
            quantity=final_row[2],
            lot_number=final_row[3],
            expiration_date=final_row[4],
            updated_at=str(final_row[5])
        )


@app.get("/medications", response_model=list[MedicationItem], operation_id="find_medications")
def find_medications(name: str = Query(..., description="Medication name to search for")):
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, name 
                FROM medications 
                WHERE name LIKE :pattern
            """),
            {"pattern": f"%{name}%"}
        ).fetchall()
        
        return [MedicationItem(id=r[0], name=r[1]) for r in rows]
