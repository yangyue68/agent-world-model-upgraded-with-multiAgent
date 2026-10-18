import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ProductResponse(BaseModel):
    id: int
    sku: str
    name: str
    retail_price: float
    reorder_point: int


class SupplierResponse(BaseModel):
    id: int
    name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None


class WarehouseResponse(BaseModel):
    id: int
    name: str
    location: Optional[str] = None


class UpdateProductRequest(BaseModel):
    retail_price: Optional[float] = None
    reorder_point: Optional[int] = None
    supplier_id: Optional[int] = None


class ProductSupplierResponse(BaseModel):
    id: int
    product_id: int
    supplier_id: int
    warehouse_id: int
    is_preferred: bool


class UpdateProductSupplierRequest(BaseModel):
    is_preferred: bool


@app.put("/products/{product_id}", response_model=ProductResponse, operation_id="update_product")
def update_product(product_id: int, body: UpdateProductRequest):
    with engine.connect() as conn:
        # Check if product exists
        result = conn.execute(text("SELECT id FROM products WHERE id = :id"), {"id": product_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Product not found")

        updates = []
        params = {"id": product_id}

        if body.retail_price is not None:
            updates.append("retail_price = :retail_price")
            params["retail_price"] = body.retail_price

        if body.reorder_point is not None:
            updates.append("reorder_point = :reorder_point")
            params["reorder_point"] = body.reorder_point

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            sql = f"UPDATE products SET {', '.join(updates)} WHERE id = :id"
            conn.execute(text(sql), params)
            conn.commit()

        # Fetch updated product
        result = conn.execute(
            text("SELECT id, sku, name, retail_price, reorder_point FROM products WHERE id = :id"),
            {"id": product_id},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Product not found")

        return ProductResponse(
            id=row[0],
            sku=row[1],
            name=row[2],
            retail_price=row[3],
            reorder_point=row[4],
        )


@app.get("/products", response_model=List[ProductResponse], operation_id="find_products_by_sku")
def find_products_by_sku(sku: str = Query(...)):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, sku, name, retail_price, reorder_point FROM products WHERE sku = :sku"),
            {"sku": sku},
        )
        rows = result.fetchall()
        return [
            ProductResponse(
                id=r[0],
                sku=r[1],
                name=r[2],
                retail_price=r[3],
                reorder_point=r[4],
            )
            for r in rows
        ]


@app.get("/suppliers", response_model=List[SupplierResponse], operation_id="find_suppliers_by_name")
def find_suppliers_by_name(name: str = Query(...)):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, name, contact_name, email, phone, address FROM suppliers WHERE name = :name"),
            {"name": name},
        )
        rows = result.fetchall()
        return [
            SupplierResponse(
                id=r[0],
                name=r[1],
                contact_name=r[2],
                email=r[3],
                phone=r[4],
                address=r[5],
            )
            for r in rows
        ]


@app.get("/warehouses", response_model=List[WarehouseResponse], operation_id="find_warehouse_by_name")
def find_warehouse_by_name(name: str = Query(...)):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, name, location FROM warehouses WHERE name = :name"),
            {"name": name},
        )
        rows = result.fetchall()
        return [
            WarehouseResponse(
                id=r[0],
                name=r[1],
                location=r[2],
            )
            for r in rows
        ]


@app.put("/product_suppliers/{product_supplier_id}", response_model=ProductSupplierResponse, operation_id="update_product_supplier")
def update_product_supplier(product_supplier_id: int, body: UpdateProductSupplierRequest):
    with engine.connect() as conn:
        # Check if record exists
        result = conn.execute(
            text("SELECT id FROM product_suppliers WHERE id = :id"),
            {"id": product_supplier_id},
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Product supplier record not found")

        is_pref_val = 1 if body.is_preferred else 0
        conn.execute(
            text("UPDATE product_suppliers SET is_preferred = :is_preferred, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"is_preferred": is_pref_val, "id": product_supplier_id},
        )
        conn.commit()

        # Fetch updated record
        result = conn.execute(
            text("SELECT id, product_id, supplier_id, warehouse_id, is_preferred FROM product_suppliers WHERE id = :id"),
            {"id": product_supplier_id},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Product supplier record not found")

        return ProductSupplierResponse(
            id=row[0],
            product_id=row[1],
            supplier_id=row[2],
            warehouse_id=row[3],
            is_preferred=bool(row[4]),
        )
