import os
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AdministerMedicationRequest(BaseModel):
    medication_name: str
    lot_number: str
    dose_amount: float
    dose_unit: str


@app.post(
    "/patients/{patient_id}/encounters/{encounter_id}/administer-medication",
    operation_id="administer_medication"
)
def administer_medication(
    patient_id: int,
    encounter_id: str,
    body: AdministerMedicationRequest,
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        administered_by = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.begin() as conn:
        # Verify patient exists
        res = conn.execute(text("SELECT id FROM patients WHERE id = :pid"), {"pid": patient_id})
        if res.fetchone() is None:
            raise HTTPException(status_code=404, detail="Patient not found")

        # Verify encounter exists and belongs to patient
        res = conn.execute(
            text("SELECT id FROM encounters WHERE id = :eid AND patient_id = :pid"),
            {"eid": encounter_id, "pid": patient_id}
        )
        if res.fetchone() is None:
            raise HTTPException(status_code=404, detail="Encounter not found for this patient")

        # Get medication ID by name
        res = conn.execute(
            text("SELECT id FROM medications WHERE name = :name"),
            {"name": body.medication_name}
        )
        med_row = res.fetchone()
        if med_row is None:
            raise HTTPException(status_code=404, detail="Medication not found")
        medication_id = med_row[0]

        # Get inventory lot details
        res = conn.execute(
            text("""
                SELECT id, quantity_on_hand 
                FROM inventory_lots 
                WHERE medication_id = :mid AND lot_number = :ln
            """),
            {"mid": medication_id, "ln": body.lot_number}
        )
        lot_row = res.fetchone()
        if lot_row is None:
            raise HTTPException(status_code=404, detail="Inventory lot not found")
        
        lot_id = lot_row[0]
        current_qty = lot_row[1]

        # Check sufficient stock
        if current_qty < 1:
            raise HTTPException(status_code=400, detail="Insufficient inventory")

        # Decrement inventory
        conn.execute(
            text("""
                UPDATE inventory_lots 
                SET quantity_on_hand = quantity_on_hand - 1, updated_at = :now
                WHERE id = :lid
            """),
            {"lid": lot_id, "now": datetime.utcnow().isoformat(sep=' ')}
        )

        # Record administration
        now_ts = datetime.utcnow().isoformat(sep=' ')
        conn.execute(
            text("""
                INSERT INTO medication_administrations (
                    encounter_id, patient_id, medication_id, lot_id, 
                    dose_amount, dose_unit, administered_at, administered_by, 
                    created_at, updated_at
                ) VALUES (
                    :eid, :pid, :mid, :lid, 
                    :da, :du, :aa, :aby, 
                    :ca, :ua
                )
            """),
            {
                "eid": encounter_id,
                "pid": patient_id,
                "mid": medication_id,
                "lid": lot_id,
                "da": body.dose_amount,
                "du": body.dose_unit,
                "aa": now_ts,
                "aby": administered_by,
                "ca": now_ts,
                "ua": now_ts
            }
        )

    return {"success": True, "message": "Medication administered successfully"}


@app.get("/medications/{name}", operation_id="get_medication_by_name")
def get_medication_by_name(name: str):
    with engine.connect() as conn:
        res = conn.execute(
            text("SELECT id, name, description FROM medications WHERE name = :name"),
            {"name": name}
        )
        row = res.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Medication not found")
        
        return {
            "id": row[0],
            "name": row[1],
            "description": row[2]
        }


@app.get("/inventory-lots/{medication_id}/{lot_number}", operation_id="get_inventory_lot_by_medication_and_lot")
def get_inventory_lot_by_medication_and_lot(medication_id: int, lot_number: str):
    with engine.connect() as conn:
        res = conn.execute(
            text("""
                SELECT id, medication_id, lot_number, expiration_date, quantity_on_hand 
                FROM inventory_lots 
                WHERE medication_id = :mid AND lot_number = :ln
            """),
            {"mid": medication_id, "ln": lot_number}
        )
        row = res.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Inventory lot not found")
        
        return {
            "id": row[0],
            "medication_id": row[1],
            "lot_number": row[2],
            "expiration_date": row[3].isoformat() if hasattr(row[3], 'isoformat') else str(row[3]),
            "quantity_on_hand": row[4]
        }
