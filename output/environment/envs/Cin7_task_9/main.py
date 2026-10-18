import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class StockAdjustmentRequest(BaseModel):
    adjustment_quantity: int
    note: str


class StockAdjustmentResponse(BaseModel):
    success: bool
    message: str


@app.post("/stock/adjust", response_model=StockAdjustmentResponse, operation_id="adjust_stock_level")
def adjust_stock_level(
    product_sku: str = Query(..., description="SKU of the product to adjust"),
    warehouse_code: str = Query(..., description="Code of the warehouse where the product is located"),
    bin_code: str = Query(..., description="Code of the bin within the warehouse where the product is located"),
    body: StockAdjustmentRequest = None,
):
    if body is None:
        raise HTTPException(status_code=422, detail="Body required")

    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=500, detail="Current user ID not found in environment")
    
    try:
        adjusted_by = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID format")

    db = SessionLocal()
    try:
        # Find product by SKU
        product_result = db.execute(
            text("SELECT id FROM products WHERE sku = :sku"),
            {"sku": product_sku}
        ).fetchone()
        
        if not product_result:
            return StockAdjustmentResponse(success=False, message=f"Product with SKU '{product_sku}' not found")
        
        product_id = product_result[0]

        # Find warehouse by code
        warehouse_result = db.execute(
            text("SELECT id FROM warehouses WHERE code = :code"),
            {"code": warehouse_code}
        ).fetchone()
        
        if not warehouse_result:
            return StockAdjustmentResponse(success=False, message=f"Warehouse with code '{warehouse_code}' not found")
        
        warehouse_id = warehouse_result[0]

        # Find bin by code and warehouse_id
        bin_result = db.execute(
            text("SELECT id FROM bins WHERE code = :bin_code AND warehouse_id = :warehouse_id"),
            {"bin_code": bin_code, "warehouse_id": warehouse_id}
        ).fetchone()
        
        if not bin_result:
            return StockAdjustmentResponse(success=False, message=f"Bin with code '{bin_code}' not found in warehouse '{warehouse_code}'")
        
        bin_id = bin_result[0]

        # Find stock level for this product, warehouse, and bin
        stock_result = db.execute(
            text("""
                SELECT id, on_hand_quantity 
                FROM stock_levels 
                WHERE product_id = :product_id 
                  AND warehouse_id = :warehouse_id 
                  AND bin_id = :bin_id
            """),
            {"product_id": product_id, "warehouse_id": warehouse_id, "bin_id": bin_id}
        ).fetchone()
        
        if not stock_result:
            return StockAdjustmentResponse(success=False, message=f"No stock record found for product '{product_sku}' in warehouse '{warehouse_code}', bin '{bin_code}'")
        
        stock_id = stock_result[0]
        current_on_hand = stock_result[1]

        # Calculate new quantity
        new_on_hand = current_on_hand + body.adjustment_quantity
        
        # Check constraint: on_hand_quantity >= 0
        if new_on_hand < 0:
            return StockAdjustmentResponse(
                success=False, 
                message=f"Adjustment would result in negative stock ({current_on_hand} + {body.adjustment_quantity} = {new_on_hand}). Not allowed."
            )

        # Update stock level
        db.execute(
            text("""
                UPDATE stock_levels 
                SET on_hand_quantity = :new_qty, updated_at = CURRENT_TIMESTAMP 
                WHERE id = :id
            """),
            {"new_qty": new_on_hand, "id": stock_id}
        )

        # Insert stock adjustment record
        db.execute(
            text("""
                INSERT INTO stock_adjustments (
                    product_id, warehouse_id, bin_id, adjustment_quantity, reason, note, adjusted_by, created_at, updated_at
                ) VALUES (
                    :product_id, :warehouse_id, :bin_id, :adjustment_quantity, :reason, :note, :adjusted_by, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """),
            {
                "product_id": product_id,
                "warehouse_id": warehouse_id,
                "bin_id": bin_id,
                "adjustment_quantity": body.adjustment_quantity,
                "reason": "Manual Adjustment",
                "note": body.note,
                "adjusted_by": adjusted_by,
            }
        )

        db.commit()

        return StockAdjustmentResponse(
            success=True,
            message=f"Stock adjusted successfully. New on-hand quantity: {new_on_hand}"
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
