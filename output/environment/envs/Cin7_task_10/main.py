import os
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Initialize FastAPI app
app = FastAPI()

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()

# Pydantic Models for Response
class VariantDetails(BaseModel):
    id: int
    product_id: int
    sku: str
    name: str
    price: float

class StockLevelDetails(BaseModel):
    id: int
    variant_id: int
    quantity: int
    location: str

class SalesOrderLineDetails(BaseModel):
    id: int
    sales_order_id: int
    variant_id: int
    quantity: int
    price: float

class PurchaseOrderLineDetails(BaseModel):
    id: int
    purchase_order_id: int
    variant_id: int
    quantity: int
    price: float

class GetVariantResponse(BaseModel):
    variant: VariantDetails
    stock_level: Optional[StockLevelDetails]
    sales_order_lines: List[SalesOrderLineDetails]
    purchase_order_lines: List[PurchaseOrderLineDetails]

class DeleteVariantResponse(BaseModel):
    message: str
    deleted_variant_id: int


@app.get("/products/{product_id}/variants/{variant_sku}", response_model=GetVariantResponse, operation_id="get_product_variant_details")
def get_product_variant_details(product_id: int, variant_sku: str, db: Session = Depends(get_db)):
    # 1. Fetch the variant
    stmt = text("""
        SELECT id, product_id, sku, name, price 
        FROM product_variants 
        WHERE product_id = :product_id AND sku = :sku
    """)
    result = db.execute(stmt, {"product_id": product_id, "sku": variant_sku}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Product variant not found")
    
    variant_data = {
        "id": result.id,
        "product_id": result.product_id,
        "sku": result.sku,
        "name": result.name,
        "price": result.price
    }
    variant_id = result.id

    # 2. Fetch Stock Level (assuming one record per variant based on schema/response structure)
    stmt_stock = text("""
        SELECT id, variant_id, quantity, location 
        FROM stock_levels 
        WHERE variant_id = :variant_id
    """)
    stock_result = db.execute(stmt_stock, {"variant_id": variant_id}).fetchone()
    
    stock_level_data = None
    if stock_result:
        stock_level_data = {
            "id": stock_result.id,
            "variant_id": stock_result.variant_id,
            "quantity": stock_result.quantity,
            "location": stock_result.location
        }

    # 3. Fetch Open Sales Order Lines
    # Join with sales_orders to filter by status 'open'
    stmt_sales = text("""
        SELECT sol.id, sol.sales_order_id, sol.variant_id, sol.quantity, sol.price
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        WHERE sol.variant_id = :variant_id AND so.status = 'open'
    """)
    sales_results = db.execute(stmt_sales, {"variant_id": variant_id}).fetchall()
    
    sales_order_lines_data = [
        {
            "id": row.id,
            "sales_order_id": row.sales_order_id,
            "variant_id": row.variant_id,
            "quantity": row.quantity,
            "price": row.price
        } for row in sales_results
    ]

    # 4. Fetch Open Purchase Order Lines
    # Join with purchase_orders to filter by status 'open'
    stmt_purchase = text("""
        SELECT pol.id, pol.purchase_order_id, pol.variant_id, pol.quantity, pol.price
        FROM purchase_order_lines pol
        JOIN purchase_orders po ON pol.purchase_order_id = po.id
        WHERE pol.variant_id = :variant_id AND po.status = 'open'
    """)
    purchase_results = db.execute(stmt_purchase, {"variant_id": variant_id}).fetchall()
    
    purchase_order_lines_data = [
        {
            "id": row.id,
            "purchase_order_id": row.purchase_order_id,
            "variant_id": row.variant_id,
            "quantity": row.quantity,
            "price": row.price
        } for row in purchase_results
    ]

    return GetVariantResponse(
        variant=VariantDetails(**variant_data),
        stock_level=StockLevelDetails(**stock_level_data) if stock_level_data else None,
        sales_order_lines=[SalesOrderLineDetails(**line) for line in sales_order_lines_data],
        purchase_order_lines=[PurchaseOrderLineDetails(**line) for line in purchase_order_lines_data]
    )


@app.delete("/products/{product_id}/variants/{variant_sku}", response_model=DeleteVariantResponse, operation_id="delete_product_variant")
def delete_product_variant(product_id: int, variant_sku: str, db: Session = Depends(get_db)):
    # 1. Verify the variant exists and belongs to the product
    stmt = text("""
        SELECT id 
        FROM product_variants 
        WHERE product_id = :product_id AND sku = :sku
    """)
    result = db.execute(stmt, {"product_id": product_id, "sku": variant_sku}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Product variant not found")
    
    variant_id = result.id

    # 2. Check for dependencies
    
    # Check Stock Levels
    stmt_stock = text("SELECT count(*) as cnt FROM stock_levels WHERE variant_id = :variant_id")
    stock_count = db.execute(stmt_stock, {"variant_id": variant_id}).scalar()
    if stock_count > 0:
        raise HTTPException(status_code=409, detail="Cannot delete variant: associated stock levels exist")

    # Check Open Sales Orders
    stmt_sales = text("""
        SELECT count(*) as cnt 
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        WHERE sol.variant_id = :variant_id AND so.status = 'open'
    """)
    open_sales_count = db.execute(stmt_sales, {"variant_id": variant_id}).scalar()
    if open_sales_count > 0:
        raise HTTPException(status_code=409, detail="Cannot delete variant: associated open sales orders exist")

    # Check Open Purchase Orders
    stmt_purchase = text("""
        SELECT count(*) as cnt 
        FROM purchase_order_lines pol
        JOIN purchase_orders po ON pol.purchase_order_id = po.id
        WHERE pol.variant_id = :variant_id AND po.status = 'open'
    """)
    open_purchase_count = db.execute(stmt_purchase, {"variant_id": variant_id}).scalar()
    if open_purchase_count > 0:
        raise HTTPException(status_code=409, detail="Cannot delete variant: associated open purchase orders exist")

    # 3. Delete the variant
    # Note: Foreign keys are ON DELETE CASCADE for stock_levels, but RESTRICT for order lines.
    # Since we verified no open order lines exist, and stock levels are empty, deletion should succeed.
    # However, there might be closed order lines referencing this variant. The spec says "verify it has no ... open sales orders".
    # It does NOT say verify no *closed* sales orders. But the FK constraint on sales_order_lines is RESTRICT.
    # If there are closed sales orders, the DB will prevent deletion due to RESTRICT.
    # The task description implies we only check for OPEN ones. If closed ones exist, the API might fail at DB level or we need to handle it.
    # Given the strict instruction "verify it has no stock levels, open sales orders, or purchase order lines", 
    # and the FK constraints, if closed orders exist, deletion will fail with IntegrityError.
    # We should catch that or assume the test data doesn't have closed orders for this variant, or the requirement implies 
    # checking ALL references? No, spec explicitly says "open".
    # Let's stick to the explicit checks. If DB blocks it due to closed orders, it's a business logic conflict in the spec vs schema.
    # Usually, you can't delete a variant referenced by history. But let's follow the prompt's verification steps strictly.
    
    try:
        del_stmt = text("DELETE FROM product_variants WHERE id = :variant_id")
        db.execute(del_stmt, {"variant_id": variant_id})
        db.commit()
    except Exception as e:
        db.rollback()
        # If it fails due to foreign key constraints from non-open orders, report error
        raise HTTPException(status_code=409, detail=f"Could not delete variant due to database constraints: {str(e)}")

    return DeleteVariantResponse(
        message="Product variant deleted successfully",
        deleted_variant_id=variant_id
    )
