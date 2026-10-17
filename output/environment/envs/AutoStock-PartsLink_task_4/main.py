import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine: Engine = create_engine(DATABASE_PATH)

app = FastAPI(title="AutoStock PartsLink API")


class PartResponse(BaseModel):
    id: int
    part_number: str


class InventoryDetail(BaseModel):
    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    bin_id: Optional[int]
    bin_code: Optional[str]
    quantity_on_hand: int
    quantity_reserved: int
    quantity_committed: int
    current_stock_level: int


@app.get("/parts/by-number/{part_number}", response_model=PartResponse, operation_id="get_part_by_number", tags=["PrimaryResource"])
def get_part_by_number(part_number: str):
    query = text("SELECT id, part_number FROM parts WHERE part_number = :part_number")
    with engine.connect() as conn:
        result = conn.execute(query, {"part_number": part_number}).fetchone()
    
    if result is None:
        raise HTTPException(status_code=404, detail=f"Part number '{part_number}' not found")
    
    return PartResponse(id=result[0], part_number=result[1])


@app.get("/inventory/part/{part_number}", response_model=List[InventoryDetail], operation_id="get_part_inventory_details", tags=["PrimaryResource"])
def get_part_inventory_details(part_number: str):
    # First, resolve the part number to a part ID
    part_query = text("SELECT id FROM parts WHERE part_number = :part_number")
    with engine.connect() as conn:
        part_result = conn.execute(part_query, {"part_number": part_number}).fetchone()
        
        if part_result is None:
            raise HTTPException(status_code=404, detail=f"Part number '{part_number}' not found")
        
        part_id = part_result[0]
        
        # Now retrieve inventory details across all warehouses for this part
        inventory_query = text("""
            SELECT 
                w.id AS warehouse_id,
                w.code AS warehouse_code,
                w.name AS warehouse_name,
                b.id AS bin_id,
                b.bin_code AS bin_code,
                i.quantity_on_hand,
                i.quantity_reserved,
                i.quantity_committed,
                (i.quantity_on_hand - i.quantity_reserved - i.quantity_committed) AS current_stock_level
            FROM inventory i
            JOIN warehouses w ON i.warehouse_id = w.id
            LEFT JOIN bins b ON i.bin_id = b.id
            WHERE i.part_id = :part_id
            ORDER BY w.id, b.id
        """)
        
        results = conn.execute(inventory_query, {"part_id": part_id}).fetchall()
        
        inventory_details = []
        for row in results:
            inventory_details.append(InventoryDetail(
                warehouse_id=row[0],
                warehouse_code=row[1],
                warehouse_name=row[2],
                bin_id=row[3],
                bin_code=row[4],
                quantity_on_hand=row[5],
                quantity_reserved=row[6],
                quantity_committed=row[7],
                current_stock_level=row[8]
            ))
        
        return inventory_details
