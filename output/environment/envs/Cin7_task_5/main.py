import os
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from datetime import datetime

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()

# --- Pydantic Models ---

class SupplierCreate(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[str] = None
    currency: Optional[str] = "USD"

class SupplierResponse(BaseModel):
    id: int
    name: str
    email: str
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[str] = None
    currency: Optional[str] = None

class ProductCreate(BaseModel):
    sku: str
    name: str
    description: Optional[str] = None
    product_type: str

class ProductResponse(BaseModel):
    id: int
    sku: str
    name: str
    description: Optional[str] = None
    product_type: str

class BOMCreate(BaseModel):
    product_sku: str
    component_sku: str
    quantity: float

class BOMResponse(BaseModel):
    id: int
    product_id: int
    component_id: int
    quantity: float

class POLineCreate(BaseModel):
    product_sku: str
    quantity: int
    unit_price: float

class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    lines: List[POLineCreate]
    expected_delivery_date: str

class PurchaseOrderResponse(BaseModel):
    id: int
    supplier_id: int
    order_date: str
    expected_delivery_date: str
    status: str
    total_amount: float


# --- Helper Functions ---

def get_product_id_by_sku(sku: str) -> int:
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id FROM products WHERE sku = :sku"), {"sku": sku})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Product with SKU '{sku}' not found")
        return row[0]

def get_supplier_by_id(supplier_id: int) -> dict:
    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM suppliers WHERE id = :id"), {"id": supplier_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Supplier with ID {supplier_id} not found")
        return dict(row._mapping)


# --- Endpoints ---

@app.post("/suppliers", response_model=SupplierResponse, operation_id="create_supplier")
def create_supplier(supplier_in: SupplierCreate):
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO suppliers (name, email, phone, address, payment_terms, currency)
                VALUES (:name, :email, :phone, :address, :payment_terms, :currency)
            """),
            {
                "name": supplier_in.name,
                "email": supplier_in.email,
                "phone": supplier_in.phone,
                "address": supplier_in.address,
                "payment_terms": supplier_in.payment_terms,
                "currency": supplier_in.currency or "USD"
            }
        )
        new_id = result.lastrowid
        # Fetch the created record to ensure we return consistent data
        res = conn.execute(text("SELECT * FROM suppliers WHERE id = :id"), {"id": new_id})
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created supplier")
        
        return SupplierResponse(
            id=row.id,
            name=row.name,
            email=row.email,
            phone=row.phone,
            address=row.address,
            payment_terms=row.payment_terms,
            currency=row.currency
        )

@app.post("/products", response_model=ProductResponse, operation_id="create_product")
def create_product(product_in: ProductCreate):
    # Validate product_type against CHECK constraint values from schema
    valid_types = ['manufactured', 'component', 'raw_material']
    if product_in.product_type not in valid_types:
        raise HTTPException(status_code=422, detail=f"Invalid product_type. Must be one of {valid_types}")

    with engine.begin() as conn:
        try:
            result = conn.execute(
                text("""
                    INSERT INTO products (sku, name, description, product_type)
                    VALUES (:sku, :name, :description, :product_type)
                """),
                {
                    "sku": product_in.sku,
                    "name": product_in.name,
                    "description": product_in.description,
                    "product_type": product_in.product_type
                }
            )
            new_id = result.lastrowid
            res = conn.execute(text("SELECT * FROM products WHERE id = :id"), {"id": new_id})
            row = res.fetchone()
            if not row:
                raise HTTPException(status_code=500, detail="Failed to retrieve created product")
            
            return ProductResponse(
                id=row.id,
                sku=row.sku,
                name=row.name,
                description=row.description,
                product_type=row.product_type
            )
        except Exception as e:
            if "UNIQUE constraint failed: products.sku" in str(e):
                raise HTTPException(status_code=409, detail=f"SKU '{product_in.sku}' already exists")
            raise e

@app.post("/boms", response_model=BOMResponse, operation_id="create_bom")
def create_bom(bom_in: BOMCreate):
    product_id = get_product_id_by_sku(bom_in.product_sku)
    component_id = get_product_id_by_sku(bom_in.component_sku)

    if bom_in.quantity <= 0:
        raise HTTPException(status_code=422, detail="Quantity must be greater than 0")

    with engine.begin() as conn:
        try:
            result = conn.execute(
                text("""
                    INSERT INTO bills_of_materials (product_id, component_id, quantity)
                    VALUES (:product_id, :component_id, :quantity)
                """),
                {
                    "product_id": product_id,
                    "component_id": component_id,
                    "quantity": bom_in.quantity
                }
            )
            new_id = result.lastrowid
            res = conn.execute(text("SELECT * FROM bills_of_materials WHERE id = :id"), {"id": new_id})
            row = res.fetchone()
            if not row:
                raise HTTPException(status_code=500, detail="Failed to retrieve created BOM entry")
            
            return BOMResponse(
                id=row.id,
                product_id=row.product_id,
                component_id=row.component_id,
                quantity=row.quantity
            )
        except Exception as e:
            if "UNIQUE constraint failed: bills_of_materials.product_id, bills_of_materials.component_id" in str(e):
                raise HTTPException(status_code=409, detail="BOM entry for this product and component already exists")
            raise e

@app.post("/purchase_orders", response_model=PurchaseOrderResponse, operation_id="create_purchase_order")
def create_purchase_order(po_in: PurchaseOrderCreate):
    # Verify supplier exists
    supplier = get_supplier_by_id(po_in.supplier_id)
    
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    # Calculate total amount and prepare line items
    total_amount = 0.0
    line_items_data = []
    
    for line in po_in.lines:
        product_id = get_product_id_by_sku(line.product_sku)
        line_total = line.quantity * line.unit_price
        total_amount += line_total
        line_items_data.append({
            "product_id": product_id,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "line_total": line_total
        })

    # Parse date to ensure it's valid, though SQLite stores as string usually
    # The spec says format date, so we pass the string directly
    
    with engine.begin() as conn:
        # Insert purchase order header
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text("""
                INSERT INTO purchase_orders (supplier_id, order_date, expected_delivery_date, status, total_amount, created_by)
                VALUES (:supplier_id, :order_date, :expected_delivery_date, :status, :total_amount, :created_by)
            """),
            {
                "supplier_id": po_in.supplier_id,
                "order_date": now,
                "expected_delivery_date": po_in.expected_delivery_date,
                "status": "draft",
                "total_amount": total_amount,
                "created_by": current_user_id
            }
        )
        po_id = result.lastrowid
        
        # Insert purchase order lines
        for item in line_items_data:
            conn.execute(
                text("""
                    INSERT INTO purchase_order_lines (purchase_order_id, product_id, quantity, unit_price, line_total)
                    VALUES (:po_id, :product_id, :quantity, :unit_price, :line_total)
                """),
                {
                    "po_id": po_id,
                    "product_id": item["product_id"],
                    "quantity": item["quantity"],
                    "unit_price": item["unit_price"],
                    "line_total": item["line_total"]
                }
            )
        
        # Fetch the created PO to return correct details
        res = conn.execute(text("SELECT * FROM purchase_orders WHERE id = :id"), {"id": po_id})
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created purchase order")
            
        return PurchaseOrderResponse(
            id=row.id,
            supplier_id=row.supplier_id,
            order_date=row.order_date.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(row.order_date, 'strftime') else str(row.order_date),
            expected_delivery_date=str(row.expected_delivery_date),
            status=row.status,
            total_amount=row.total_amount
        )
