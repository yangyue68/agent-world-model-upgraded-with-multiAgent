import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UpdateSalesOrderLineRequest(BaseModel):
    quantity: Optional[int] = None
    discount_percent: Optional[float] = None


class SalesOrderLineResponse(BaseModel):
    id: int
    sales_order_id: int
    product_id: int
    quantity: int
    unit_price: float
    discount_percent: float
    line_total: float


class UpdateShippingAddressRequest(BaseModel):
    shipping_address: str


class SalesOrderResponse(BaseModel):
    id: int
    order_number: str
    customer_id: int
    status: str
    order_date: str
    shipping_address: Optional[str]
    created_by: int


@app.put(
    "/sales-orders/{order_id}/lines/{line_id}",
    response_model=SalesOrderLineResponse,
    operation_id="update_sales_order_line",
)
def update_sales_order_line(
    order_id: int = Path(...),
    line_id: int = Path(...),
    body: UpdateSalesOrderLineRequest = ...,
):
    with Session(engine) as db:
        # Verify the line exists and belongs to the given order
        result = db.execute(
            text(
                """
                SELECT id, sales_order_id, product_id, quantity, unit_price, discount_percent, line_total
                FROM sales_order_lines
                WHERE id = :line_id AND sales_order_id = :order_id
                """
            ),
            {"line_id": line_id, "order_id": order_id},
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Sales order line not found")

        current_quantity = result[3]
        current_discount = result[5]

        new_quantity = body.quantity if body.quantity is not None else current_quantity
        new_discount = (
            body.discount_percent if body.discount_percent is not None else current_discount
        )

        # Validate constraints
        if new_quantity <= 0:
            raise HTTPException(status_code=400, detail="Quantity must be greater than 0")
        if new_discount < 0 or new_discount > 100:
            raise HTTPException(
                status_code=400, detail="Discount percent must be between 0 and 100"
            )

        # Update the line item
        db.execute(
            text(
                """
                UPDATE sales_order_lines
                SET quantity = :quantity, discount_percent = :discount, updated_at = CURRENT_TIMESTAMP
                WHERE id = :line_id
                """
            ),
            {
                "quantity": new_quantity,
                "discount": new_discount,
                "line_id": line_id,
            },
        )
        db.commit()

        # Fetch updated row to get computed line_total
        updated_result = db.execute(
            text(
                """
                SELECT id, sales_order_id, product_id, quantity, unit_price, discount_percent, line_total
                FROM sales_order_lines
                WHERE id = :line_id
                """
            ),
            {"line_id": line_id},
        ).fetchone()

        return SalesOrderLineResponse(
            id=updated_result[0],
            sales_order_id=updated_result[1],
            product_id=updated_result[2],
            quantity=updated_result[3],
            unit_price=float(updated_result[4]),
            discount_percent=float(updated_result[5]),
            line_total=float(updated_result[6]),
        )


@app.put(
    "/sales-orders/{order_id}",
    response_model=SalesOrderResponse,
    operation_id="update_sales_order_shipping_address",
)
def update_sales_order_shipping_address(
    order_id: int = Path(...),
    body: UpdateShippingAddressRequest = ...,
):
    with Session(engine) as db:
        # Verify the order exists
        result = db.execute(
            text(
                """
                SELECT id, order_number, customer_id, status, order_date, shipping_address, created_by
                FROM sales_orders
                WHERE id = :order_id
                """
            ),
            {"order_id": order_id},
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Sales order not found")

        # Update shipping address
        db.execute(
            text(
                """
                UPDATE sales_orders
                SET shipping_address = :shipping_address, updated_at = CURRENT_TIMESTAMP
                WHERE id = :order_id
                """
            ),
            {"shipping_address": body.shipping_address, "order_id": order_id},
        )
        db.commit()

        # Fetch updated row
        updated_result = db.execute(
            text(
                """
                SELECT id, order_number, customer_id, status, order_date, shipping_address, created_by
                FROM sales_orders
                WHERE id = :order_id
                """
            ),
            {"order_id": order_id},
        ).fetchone()

        return SalesOrderResponse(
            id=updated_result[0],
            order_number=updated_result[1],
            customer_id=updated_result[2],
            status=updated_result[3],
            order_date=str(updated_result[4]),
            shipping_address=updated_result[5],
            created_by=updated_result[6],
        )
