import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class GravityReadingRequest(BaseModel):
    gravity: float
    temperature: float | None = None


class DryHopAdditionRequest(BaseModel):
    hop_name: str
    amount: float


def get_current_user_id() -> int:
    """Retrieve the current user ID from the environment."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.post("/batches/{batch_id}/gravity_readings", operation_id="log_gravity_reading")
def log_gravity_reading(
    batch_id: int = Path(..., description="Internal ID of the batch"),
    payload: GravityReadingRequest = Body(...)
):
    user_id = get_current_user_id()
    
    # Check if batch exists and is in 'fermentation' stage
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT stage FROM batches WHERE id = :bid"),
            {"bid": batch_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Batch not found")
        
        if result[0] != "fermentation":
            raise HTTPException(status_code=400, detail="Batch must be in fermentation stage to log gravity readings")
        
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Insert gravity reading
        insert_stmt = text("""
            INSERT INTO gravity_readings (batch_id, gravity, temperature, recorded_by, recorded_at, created_at)
            VALUES (:bid, :grav, :temp, :uid, :now, :now)
        """)
        
        params = {
            "bid": batch_id,
            "grav": payload.gravity,
            "temp": payload.temperature,
            "uid": user_id,
            "now": now
        }
        
        res = conn.execute(insert_stmt, params)
        new_id = res.lastrowid
        
        conn.commit()
        
        # Fetch the inserted record to return full details
        fetch_stmt = text("""
            SELECT id, batch_id, gravity, temperature, recorded_by, recorded_at 
            FROM gravity_readings 
            WHERE id = :rid
        """)
        row = conn.execute(fetch_stmt, {"rid": new_id}).fetchone()
        
        return {
            "id": row[0],
            "batch_id": row[1],
            "gravity": row[2],
            "temperature": row[3],
            "recorded_by": row[4],
            "recorded_at": row[5]
        }


@app.patch("/batches/{batch_id}", operation_id="transition_batch_stage")
def transition_batch_stage(
    batch_id: int = Path(..., description="Internal ID of the batch")
):
    user_id = get_current_user_id()
    
    with engine.connect() as conn:
        # Check if batch exists
        result = conn.execute(
            text("SELECT stage FROM batches WHERE id = :bid"),
            {"bid": batch_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Batch not found")
        
        current_stage = result[0]
        
        # Logic for transitioning stages based on typical brewing workflow
        # The spec says "Move a batch from fermentation to conditioning stage"
        # We should validate that the transition is valid.
        # Assuming linear progression: brewing -> fermentation -> conditioning -> packaging -> complete
        stage_order = ['brewing', 'fermentation', 'conditioning', 'packaging', 'complete']
        
        if current_stage == 'complete':
             raise HTTPException(status_code=400, detail="Batch is already complete")
             
        try:
            idx = stage_order.index(current_stage)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown current stage: {current_stage}")
            
        if idx >= len(stage_order) - 1:
            raise HTTPException(status_code=400, detail="Cannot transition further")
            
        next_stage = stage_order[idx + 1]
        
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        update_stmt = text("""
            UPDATE batches 
            SET stage = :new_stage, updated_at = :now 
            WHERE id = :bid
        """)
        
        conn.execute(update_stmt, {
            "new_stage": next_stage,
            "bid": batch_id,
            "now": now
        })
        
        conn.commit()
        
        # Fetch updated batch info
        fetch_stmt = text("""
            SELECT id, batch_code, stage, updated_at 
            FROM batches 
            WHERE id = :bid
        """)
        row = conn.execute(fetch_stmt, {"bid": batch_id}).fetchone()
        
        return {
            "id": row[0],
            "batch_code": row[1],
            "stage": row[2],
            "updated_at": row[3]
        }


@app.post("/batches/{batch_id}/dry_hop_additions", operation_id="add_dry_hops")
def add_dry_hops(
    batch_id: int = Path(..., description="Internal ID of the batch"),
    payload: DryHopAdditionRequest = Body(...)
):
    user_id = get_current_user_id()
    
    with engine.connect() as conn:
        # Check if batch exists and is in 'conditioning' stage
        result = conn.execute(
            text("SELECT stage FROM batches WHERE id = :bid"),
            {"bid": batch_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Batch not found")
        
        if result[0] != "conditioning":
            raise HTTPException(status_code=400, detail="Batch must be in conditioning stage to add dry hops")
        
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Insert dry hop addition
        insert_stmt = text("""
            INSERT INTO dry_hop_additions (batch_id, hop_name, amount, added_by, added_at, created_at, updated_at)
            VALUES (:bid, :hop, :amt, :uid, :now, :now, :now)
        """)
        
        params = {
            "bid": batch_id,
            "hop": payload.hop_name,
            "amt": payload.amount,
            "uid": user_id,
            "now": now
        }
        
        res = conn.execute(insert_stmt, params)
        new_id = res.lastrowid
        
        conn.commit()
        
        # Fetch the inserted record
        fetch_stmt = text("""
            SELECT id, batch_id, hop_name, amount, amount_unit, added_by, added_at 
            FROM dry_hop_additions 
            WHERE id = :rid
        """)
        row = conn.execute(fetch_stmt, {"rid": new_id}).fetchone()
        
        return {
            "id": row[0],
            "batch_id": row[1],
            "hop_name": row[2],
            "amount": row[3],
            "amount_unit": row[4],
            "added_by": row[5],
            "added_at": row[6]
        }
