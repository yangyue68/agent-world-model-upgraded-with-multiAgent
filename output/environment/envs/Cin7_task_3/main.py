import os
from datetime import datetime, date
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

app = FastAPI(title="Cin7 API", version="1.0.0")


# --- Pydantic Models for Response ---

class LineItem(BaseModel):
    sku: str
    quantity_ordered: int
    quantity_allocated: int
    quantity_backordered: int

class SalesOrderResponse(BaseModel):
    order_number: str
    customer_name: str
    warehouse_name: str
    total_amount: float
    line_items: List[LineItem]


# --- Database Models (for reference only, we use raw SQL/Text) ---
# The schema is already populated, so we don't need to define models for ORM mapping if we use text(),
# but defining them helps with type hinting if we were using ORM. 
# However, the prompt says "Use existing tables and parameterized SQL". 
# We will use SQLAlchemy Core or Textual queries to fetch data.

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/sales-orders", response_model=List[SalesOrderResponse], operation_id="find_sales_orders")
def find_sales_orders(
    start_date: str = Query(..., description="Start date for filtering orders (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date for filtering orders (YYYY-MM-DD)"),
    sales_channel: Optional[str] = Query(None, description="Filter by sales channel name (e.g., 'Shopify')"),
    status: Optional[str] = Query(None, description="Filter by order status (e.g., 'Unfulfilled', 'Partially Allocated')")
):
    """
    Retrieve all sales orders from the Shopify sales channel with specific statuses and date range.
    """
    
    # Parse dates to ensure they are valid YYYY-MM-DD
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Build query conditions
    conditions = []
    params = {}

    # Filter by sales channel if provided
    if sales_channel:
        conditions.append("sc.name = :sales_channel")
        params["sales_channel"] = sales_channel

    # Filter by status if provided
    if status:
        conditions.append("so.status = :status")
        params["status"] = status
    
    # Filter by date range
    conditions.append("so.created_at >= :start_date")
    conditions.append("so.created_at <= :end_date")
    params["start_date"] = start_date + " 00:00:00"
    params["end_date"] = end_date + " 23:59:59"

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # Main query to get orders
    query_text = f"""
    SELECT 
        so.id,
        so.order_number,
        c.name AS customer_name,
        w.name AS warehouse_name,
        so.total_amount
    FROM sales_orders so
    JOIN customers c ON so.customer_id = c.id
    JOIN warehouses w ON so.warehouse_id = w.id
    JOIN sales_channels sc ON so.sales_channel_id = sc.id
    WHERE {where_clause}
    ORDER BY so.created_at DESC
    """

    db = SessionLocal()
    try:
        result = db.execute(text(query_text), params)
        orders_raw = result.fetchall()
        
        # Fetch line items for each order
        response_data = []
        for row in orders_raw:
            order_id = row[0]
            order_number = row[1]
            customer_name = row[2]
            warehouse_name = row[3]
            total_amount = row[4]

            # Get line items for this order
            line_items_query = """
            SELECT 
                p.sku,
                sol.quantity_ordered,
                sol.quantity_allocated,
                sol.quantity_backordered
            FROM sales_order_lines sol
            JOIN products p ON sol.product_id = p.id
            WHERE sol.sales_order_id = :order_id
            """
            line_items_result = db.execute(text(line_items_query), {"order_id": order_id})
            line_items_raw = line_items_result.fetchall()

            line_items = [
                LineItem(
                    sku=item[0],
                    quantity_ordered=item[1],
                    quantity_allocated=item[2],
                    quantity_backordered=item[3]
                )
                for item in line_items_raw
            ]

            response_data.append(
                SalesOrderResponse(
                    order_number=order_number,
                    customer_name=customer_name,
                    warehouse_name=warehouse_name,
                    total_amount=total_amount,
                    line_items=line_items
                )
            )
        
        return response_data
    finally:
        db.close()
