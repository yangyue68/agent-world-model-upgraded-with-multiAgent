import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class EquipmentItem(BaseModel):
    name: str
    category_id: int
    barcode: str
    location_id: int
    condition: str
    replacement_value: float


class BatchRequest(BaseModel):
    equipment_items: List[EquipmentItem]


class FailedItem(BaseModel):
    index: int
    error: str


class BatchResponse(BaseModel):
    created_count: int
    failed_items: List[FailedItem]


@app.post("/equipment/batch", response_model=BatchResponse, operation_id="create_equipment_batch")
def create_equipment_batch(request: BatchRequest):
    db = SessionLocal()
    try:
        # Resolve Category ID for "Audio Equipment" if needed, but spec says pass category_id.
        # The prompt implies we should look up or use provided IDs. 
        # However, the request body requires category_id and location_id as integers.
        # We assume the client provides correct IDs based on the schema.
        
        created_count = 0
        failed_items = []

        for index, item in enumerate(request.equipment_items):
            try:
                # Validate Condition against allowed values from DB schema CHECK constraint
                allowed_conditions = ['Excellent', 'Good', 'Fair', 'Poor', 'Damaged']
                if item.condition not in allowed_conditions:
                    raise ValueError(f"Invalid condition '{item.condition}'. Must be one of {allowed_conditions}")

                # Insert into equipment table
                # Note: is_available defaults to 1 in schema, which matches "available for booking"
                stmt = text("""
                    INSERT INTO equipment (name, category_id, barcode, location_id, condition, replacement_value, is_available)
                    VALUES (:name, :category_id, :barcode, :location_id, :condition, :replacement_value, 1)
                """)
                
                params = {
                    "name": item.name,
                    "category_id": item.category_id,
                    "barcode": item.barcode,
                    "location_id": item.location_id,
                    "condition": item.condition,
                    "replacement_value": item.replacement_value
                }
                
                result = db.execute(stmt, params)
                db.commit()
                created_count += 1
                
            except Exception as e:
                db.rollback()
                failed_items.append(FailedItem(index=index, error=str(e)))

        return BatchResponse(created_count=created_count, failed_items=failed_items)
    
    finally:
        db.close()
