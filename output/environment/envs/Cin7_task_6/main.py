import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# --- Pydantic Models ---

class BundleProductCreate(BaseModel):
    sku: str
    name: str
    sale_price: float

class BundleComponentCreate(BaseModel):
    bundle_product_id: int
    component_product_id: int
    quantity: int

class SalesOrderCreate(BaseModel):
    customer_id: int
    shipping_address: str

class SalesOrderItemCreate(BaseModel):
    sales_order_id: int
    product_id: int
    quantity: int
    unit_price: float

class StockTransferCreate(BaseModel):
    source_warehouse_id: int
    destination_warehouse_id: int

class StockTransferItemCreate(BaseModel):
    stock_transfer_id: int
    product_id: int
    quantity: int

# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def generate_next_number(db, table_name: str, prefix: str, year: int) -> str:
    """Generates the next sequential number for a given prefix and year."""
    # Find max existing number with this prefix pattern
    query = text(f"""
        SELECT MAX(CAST(SUBSTR({table_name}_number, LENGTH(:prefix) + 1) AS INTEGER)) 
        FROM {table_name} 
        WHERE {table_name}_number LIKE :pattern
    """)
    
    # For sales_orders: order_number
    # For stock_transfers: transfer_number
    
    if table_name == "sales_orders":
        col = "order_number"
    elif table_name == "stock_transfers":
        col = "transfer_number"
    else:
        raise ValueError("Unknown table for numbering")

    query = text(f"""
        SELECT MAX(CAST(SUBSTR({col}, LENGTH(:prefix) + 1) AS INTEGER)) 
        FROM {table_name} 
        WHERE {col} LIKE :pattern
    """)
    
    result = db.execute(query, {"prefix": prefix, "pattern": f"{prefix}%"}).scalar()
    
    if result is None:
        next_num = 1
    else:
        next_num = result + 1
        
    return f"{prefix}{next_num:04d}"

# --- Endpoints ---

@app.post("/products", operation_id="create_bundle_product")
def create_bundle_product(product: BundleProductCreate):
    db = SessionLocal()
    try:
        # Check if SKU already exists
        existing = db.execute(text("SELECT id FROM products WHERE sku = :sku"), {"sku": product.sku}).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="SKU already exists")
        
        stmt = text("""
            INSERT INTO products (sku, name, type, sale_price)
            VALUES (:sku, :name, 'bundle', :sale_price)
        """)
        db.execute(stmt, {
            "sku": product.sku,
            "name": product.name,
            "sale_price": product.sale_price
        })
        db.commit()
        
        new_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        
        return {
            "id": new_id,
            "sku": product.sku,
            "name": product.name,
            "type": "bundle",
            "sale_price": product.sale_price
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/bundle_components", operation_id="add_bundle_components")
def add_bundle_components(component: BundleComponentCreate):
    db = SessionLocal()
    try:
        # Verify bundle product exists and is a bundle
        bundle = db.execute(text("SELECT type FROM products WHERE id = :id"), {"id": component.bundle_product_id}).fetchone()
        if not bundle or bundle[0] != 'bundle':
            raise HTTPException(status_code=404, detail="Bundle product not found or invalid type")
            
        # Verify component product exists
        comp_prod = db.execute(text("SELECT id FROM products WHERE id = :id"), {"id": component.component_product_id}).fetchone()
        if not comp_prod:
            raise HTTPException(status_code=404, detail="Component product not found")
            
        # Check uniqueness constraint
        existing = db.execute(text("""
            SELECT id FROM bundle_components 
            WHERE bundle_product_id = :bid AND component_product_id = :cid
        """), {"bid": component.bundle_product_id, "cid": component.component_product_id}).fetchone()
        if existing:
             raise HTTPException(status_code=400, detail="Component already added to this bundle")

        stmt = text("""
            INSERT INTO bundle_components (bundle_product_id, component_product_id, quantity)
            VALUES (:bid, :cid, :qty)
        """)
        db.execute(stmt, {
            "bid": component.bundle_product_id,
            "cid": component.component_product_id,
            "qty": component.quantity
        })
        db.commit()
        
        new_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        
        return {
            "id": new_id,
            "bundle_product_id": component.bundle_product_id,
            "component_product_id": component.component_product_id,
            "quantity": component.quantity
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/sales_orders", operation_id="create_sales_order")
def create_sales_order(order: SalesOrderCreate):
    db = SessionLocal()
    try:
        # Verify customer exists
        cust = db.execute(text("SELECT id FROM customers WHERE id = :id"), {"id": order.customer_id}).fetchone()
        if not cust:
            raise HTTPException(status_code=404, detail="Customer not found")
            
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        created_by = int(current_user_id_str) if current_user_id_str else None
        
        # Generate Order Number
        # Format: SO-YYYY-NNNN
        from datetime import datetime
        year = datetime.now().year
        prefix = f"SO-{year}-"
        order_number = generate_next_number(db, "sales_orders", prefix, year)
        
        stmt = text("""
            INSERT INTO sales_orders (order_number, customer_id, shipping_address, status, created_by)
            VALUES (:num, :cust_id, :addr, 'draft', :created_by)
        """)
        db.execute(stmt, {
            "num": order_number,
            "cust_id": order.customer_id,
            "addr": order.shipping_address,
            "created_by": created_by
        })
        db.commit()
        
        new_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        
        return {
            "id": new_id,
            "order_number": order_number,
            "customer_id": order.customer_id,
            "shipping_address": order.shipping_address,
            "status": "draft"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/sales_order_items", operation_id="add_sales_order_item")
def add_sales_order_item(item: SalesOrderItemCreate):
    db = SessionLocal()
    try:
        # Verify sales order exists
        so = db.execute(text("SELECT id FROM sales_orders WHERE id = :id"), {"id": item.sales_order_id}).fetchone()
        if not so:
            raise HTTPException(status_code=404, detail="Sales order not found")
            
        # Verify product exists
        prod = db.execute(text("SELECT id FROM products WHERE id = :id"), {"id": item.product_id}).fetchone()
        if not prod:
            raise HTTPException(status_code=404, detail="Product not found")
            
        stmt = text("""
            INSERT INTO sales_order_items (sales_order_id, product_id, quantity, unit_price)
            VALUES (:so_id, :prod_id, :qty, :price)
        """)
        db.execute(stmt, {
            "so_id": item.sales_order_id,
            "prod_id": item.product_id,
            "qty": item.quantity,
            "price": item.unit_price
        })
        db.commit()
        
        new_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        
        return {
            "id": new_id,
            "sales_order_id": item.sales_order_id,
            "product_id": item.product_id,
            "quantity": item.quantity,
            "unit_price": item.unit_price
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/stock_transfers", operation_id="create_stock_transfer")
def create_stock_transfer(transfer: StockTransferCreate):
    db = SessionLocal()
    try:
        # Verify warehouses exist
        src_wh = db.execute(text("SELECT id FROM warehouses WHERE id = :id"), {"id": transfer.source_warehouse_id}).fetchone()
        dest_wh = db.execute(text("SELECT id FROM warehouses WHERE id = :id"), {"id": transfer.destination_warehouse_id}).fetchone()
        
        if not src_wh:
            raise HTTPException(status_code=404, detail="Source warehouse not found")
        if not dest_wh:
            raise HTTPException(status_code=404, detail="Destination warehouse not found")
            
        if transfer.source_warehouse_id == transfer.destination_warehouse_id:
            raise HTTPException(status_code=400, detail="Source and destination warehouses must be different")
            
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        created_by = int(current_user_id_str) if current_user_id_str else None
        
        # Generate Transfer Number
        # Format: ST-YYYY-NNNN
        from datetime import datetime
        year = datetime.now().year
        prefix = f"ST-{year}-"
        transfer_number = generate_next_number(db, "stock_transfers", prefix, year)
        
        stmt = text("""
            INSERT INTO stock_transfers (transfer_number, source_warehouse_id, destination_warehouse_id, status, created_by)
            VALUES (:num, :src, :dest, 'pending', :created_by)
        """)
        db.execute(stmt, {
            "num": transfer_number,
            "src": transfer.source_warehouse_id,
            "dest": transfer.destination_warehouse_id,
            "created_by": created_by
        })
        db.commit()
        
        new_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        
        return {
            "id": new_id,
            "transfer_number": transfer_number,
            "source_warehouse_id": transfer.source_warehouse_id,
            "destination_warehouse_id": transfer.destination_warehouse_id,
            "status": "pending"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/stock_transfer_items", operation_id="add_stock_transfer_item")
def add_stock_transfer_item(item: StockTransferItemCreate):
    db = SessionLocal()
    try:
        # Verify stock transfer exists
        st = db.execute(text("SELECT id FROM stock_transfers WHERE id = :id"), {"id": item.stock_transfer_id}).fetchone()
        if not st:
            raise HTTPException(status_code=404, detail="Stock transfer not found")
            
        # Verify product exists
        prod = db.execute(text("SELECT id FROM products WHERE id = :id"), {"id": item.product_id}).fetchone()
        if not prod:
            raise HTTPException(status_code=404, detail="Product not found")
            
        stmt = text("""
            INSERT INTO stock_transfer_items (stock_transfer_id, product_id, quantity)
            VALUES (:st_id, :prod_id, :qty)
        """)
        db.execute(stmt, {
            "st_id": item.stock_transfer_id,
            "prod_id": item.product_id,
            "qty": item.quantity
        })
        db.commit()
        
        new_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        
        return {
            "id": new_id,
            "stock_transfer_id": item.stock_transfer_id,
            "product_id": item.product_id,
            "quantity": item.quantity
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
