import os
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="AutoStock PartsLink API")


class SupplierResponse(BaseModel):
    id: int
    name: str


class WarehouseResponse(BaseModel):
    id: int
    code: str


class PurchaseOrderLineInput(BaseModel):
    part_number: str
    quantity: int


class CreatePurchaseOrderRequest(BaseModel):
    supplier_id: int
    warehouse_id: int
    requested_delivery_date: str  # YYYY-MM-DD format expected per spec
    lines: list[PurchaseOrderLineInput]


class PurchaseOrderResponse(BaseModel):
    id: int
    po_number: str
    status: str


@app.get("/suppliers", response_model=list[SupplierResponse], operation_id="find_suppliers")
def find_suppliers(name: str = Query(..., description="Supplier name")):
    with SessionLocal() as db:
        stmt = text("SELECT id, name FROM suppliers WHERE name LIKE :name_pattern")
        result = db.execute(stmt, {"name_pattern": f"%{name}%"}).fetchall()
        return [{"id": row.id, "name": row.name} for row in result]


@app.get("/warehouses", response_model=list[WarehouseResponse], operation_id="find_warehouses")
def find_warehouses(code: str = Query(..., description="Warehouse code")):
    with SessionLocal() as db:
        stmt = text("SELECT id, code FROM warehouses WHERE code LIKE :code_pattern")
        result = db.execute(stmt, {"code_pattern": f"%{code}%"}).fetchall()
        return [{"id": row.id, "code": row.code} for row in result]


@app.post("/purchase_orders", response_model=PurchaseOrderResponse, operation_id="create_purchase_order")
def create_purchase_order(payload: CreatePurchaseOrderRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        created_by = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with SessionLocal() as db:
        try:
            # Validate supplier exists
            sup_stmt = text("SELECT id FROM suppliers WHERE id = :sid")
            sup_res = db.execute(sup_stmt, {"sid": payload.supplier_id}).fetchone()
            if not sup_res:
                raise HTTPException(status_code=404, detail="Supplier not found")

            # Validate warehouse exists
            wh_stmt = text("SELECT id FROM warehouses WHERE id = :wid")
            wh_res = db.execute(wh_stmt, {"wid": payload.warehouse_id}).fetchone()
            if not wh_res:
                raise HTTPException(status_code=404, detail="Warehouse not found")

            # Parse delivery date
            try:
                req_date = date.fromisoformat(payload.requested_delivery_date)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

            # Generate PO Number based on existing max or sequence logic
            now = datetime.utcnow()
            year = now.year
            
            # Find next available number for this year
            seq_stmt = text("""
                SELECT COALESCE(MAX(CAST(SUBSTR(po_number, -4) AS INTEGER)), 0) 
                FROM purchase_orders 
                WHERE po_number LIKE :prefix
            """)
            prefix = f"PO-{year}-"
            res = db.execute(seq_stmt, {"prefix": f"{prefix}%"}).scalar()
            
            next_seq = (res or 0) + 1
            po_number = f"{prefix}{next_seq:04d}"

            # Insert purchase order
            insert_po_stmt = text("""
                INSERT INTO purchase_orders (po_number, supplier_id, warehouse_id, requested_delivery_date, status, created_by, created_at, updated_at)
                VALUES (:po_number, :supplier_id, :warehouse_id, :req_date, 'draft', :created_by, :now, :now)
            """)
            
            db.execute(insert_po_stmt, {
                "po_number": po_number,
                "supplier_id": payload.supplier_id,
                "warehouse_id": payload.warehouse_id,
                "req_date": req_date.isoformat(),
                "created_by": created_by,
                "now": now
            })
            
            po_id = db.execute(text("SELECT last_insert_rowid()")).scalar()

            # Process lines
            for line in payload.lines:
                # Look up part by part_number
                part_stmt = text("SELECT id FROM parts WHERE part_number = :pn")
                part_res = db.execute(part_stmt, {"pn": line.part_number}).fetchone()
                
                if not part_res:
                    raise HTTPException(status_code=404, detail=f"Part number {line.part_number} not found")
                
                part_id = part_res.id
                
                insert_line_stmt = text("""
                    INSERT INTO purchase_order_lines (purchase_order_id, part_id, quantity, created_at, updated_at)
                    VALUES (:po_id, :part_id, :qty, :now, :now)
                """)
                
                db.execute(insert_line_stmt, {
                    "po_id": po_id,
                    "part_id": part_id,
                    "qty": line.quantity,
                    "now": now
                })

            db.commit()

            return PurchaseOrderResponse(id=po_id, po_number=po_number, status="draft")
        
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=str(e))
