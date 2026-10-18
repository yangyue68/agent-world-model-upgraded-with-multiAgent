import os
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, ForeignKey, MetaData, Table
DATABASE_PATH = os.environ.get("DATABASE_PATH")
app = FastAPI()

engine = create_engine(DATABASE_PATH)

metadata = MetaData()

suppliers = Table('suppliers', metadata, autoload_with=engine)
warehouses = Table('warehouses', metadata, autoload_with=engine)
products = Table('products', metadata, autoload_with=engine)
product_variants = Table('product_variants', metadata, autoload_with=engine)
inventory = Table('inventory', metadata, autoload_with=engine)


class VariantSchema(BaseModel):
    sku: str
    size: Optional[str] = None
    color: Optional[str] = None


class SupplierSchema(BaseModel):
    id: int
    name: str


class InventoryItemSchema(BaseModel):
    warehouse_id: int
    warehouse_name: str
    quantity: int


class ProductSearchResult(BaseModel):
    id: int
    name: str
    variants: List[VariantSchema]
    supplier: SupplierSchema
    reorder_point: int
    inventory: List[InventoryItemSchema]


@app.get("/products/search", response_model=List[ProductSearchResult], operation_id="search_products")
def search_products(name: str = Query(..., description="Product name fragment to search for")):
    # Search for products whose name contains the search term
    stmt = text("""
        SELECT p.id, p.name, p.reorder_point, s.id as supplier_id, s.name as supplier_name
        FROM products p
        LEFT JOIN suppliers s ON p.supplier_id = s.id
        WHERE p.name LIKE :name_term
    """)
    
    with engine.connect() as conn:
        result = conn.execute(stmt, {"name_term": f"%{name}%"})
        product_rows = result.fetchall()
        
        if not product_rows:
            return []
            
        results = []
        for row in product_rows:
            product_id = row[0]
            product_name = row[1]
            reorder_point = row[2]
            supplier_id = row[3]
            supplier_name = row[4]
            
            # Fetch variants for this product
            variant_stmt = text("""
                SELECT sku, size, color
                FROM product_variants
                WHERE product_id = :pid
            """)
            variant_result = conn.execute(variant_stmt, {"pid": product_id})
            variant_rows = variant_result.fetchall()
            
            variants = []
            for v_row in variant_rows:
                variants.append(VariantSchema(
                    sku=v_row[0],
                    size=v_row[1],
                    color=v_row[2]
                ))
            
            # Fetch inventory for all variants of this product
            if variants:
                variant_ids = [v.sku for v in variants] # Using SKU as identifier isn't ideal but we need variant IDs
                # Actually, we need variant IDs from the DB. Let's fetch them properly.
                pass
            
            # Re-fetch variant IDs specifically for inventory lookup
            variant_ids_stmt = text("""
                SELECT id FROM product_variants WHERE product_id = :pid
            """)
            vid_result = conn.execute(variant_ids_stmt, {"pid": product_id})
            variant_ids = [r[0] for r in vid_result.fetchall()]
            
            inventory_items = []
            if variant_ids:
                placeholders = ','.join([':' + str(i) for i in range(len(variant_ids))])
                inv_params = {str(i): vid for i, vid in enumerate(variant_ids)}
                
                inv_stmt = text(f"""
                    SELECT w.id as warehouse_id, w.name as warehouse_name, i.quantity
                    FROM inventory i
                    JOIN warehouses w ON i.warehouse_id = w.id
                    WHERE i.variant_id IN ({placeholders})
                """)
                inv_result = conn.execute(inv_stmt, inv_params)
                inv_rows = inv_result.fetchall()
                
                for inv_row in inv_rows:
                    inventory_items.append(InventoryItemSchema(
                        warehouse_id=inv_row[0],
                        warehouse_name=inv_row[1],
                        quantity=inv_row[2]
                    ))
            
            supplier_info = SupplierSchema(id=supplier_id, name=supplier_name)
            
            product_result = ProductSearchResult(
                id=product_id,
                name=product_name,
                variants=variants,
                supplier=supplier_info,
                reorder_point=reorder_point,
                inventory=inventory_items
            )
            results.append(product_result)
            
        return results
