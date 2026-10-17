import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class InvoiceItem(BaseModel):
    id: int
    invoice_id: int
    description: str
    quantity: int
    unit_price: float
    total_price: float
    created_at: str


class InvoiceResponse(BaseModel):
    id: int
    auction_id: int
    buyer_id: int
    seller_id: int
    winning_bid_amount: float
    shipping_fee: float
    tax_amount: float
    total_amount: float
    status: str
    due_date: str
    paid_at: Optional[str]
    created_at: str
    updated_at: str
    items: List[InvoiceItem]


class PaymentMethodResponse(BaseModel):
    id: int
    user_id: int
    card_type: str
    last_four: str
    expiry_month: int
    expiry_year: int
    is_default: int
    created_at: str
    updated_at: str


class PaymentResponse(BaseModel):
    id: int
    invoice_id: int
    user_id: int
    payment_method_id: int
    amount: float
    status: str
    transaction_id: str
    paid_at: str
    created_at: str
    updated_at: str


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")


@app.get("/invoices/{invoice_id}", response_model=InvoiceResponse, operation_id="get_invoice_details")
def get_invoice_details(invoice_id: int = Path(...)):
    with engine.connect() as conn:
        # Fetch invoice
        result = conn.execute(
            text("SELECT * FROM invoices WHERE id = :id"), {"id": invoice_id}
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Invoice not found")

        invoice_data = dict(row._mapping)

        # Fetch invoice items
        items_result = conn.execute(
            text("SELECT * FROM invoice_items WHERE invoice_id = :invoice_id ORDER BY id"),
            {"invoice_id": invoice_id},
        )
        items_rows = items_result.fetchall()
        items = []
        for item_row in items_rows:
            item_dict = dict(item_row._mapping)
            items.append(InvoiceItem(**item_dict))

        # Format dates
        def format_dt(dt_val):
            if dt_val is None:
                return None
            if isinstance(dt_val, datetime):
                return dt_val.isoformat()
            return str(dt_val)

        invoice_response = InvoiceResponse(
            id=invoice_data["id"],
            auction_id=invoice_data["auction_id"],
            buyer_id=invoice_data["buyer_id"],
            seller_id=invoice_data["seller_id"],
            winning_bid_amount=float(invoice_data["winning_bid_amount"]),
            shipping_fee=float(invoice_data["shipping_fee"]),
            tax_amount=float(invoice_data["tax_amount"]),
            total_amount=float(invoice_data["total_amount"]),
            status=invoice_data["status"],
            due_date=format_dt(invoice_data["due_date"]),
            paid_at=format_dt(invoice_data["paid_at"]),
            created_at=format_dt(invoice_data["created_at"]),
            updated_at=format_dt(invoice_data["updated_at"]),
            items=items,
        )
        return invoice_response


@app.get("/payment_methods", response_model=List[PaymentMethodResponse], operation_id="list_payment_methods")
def list_payment_methods():
    current_user_id = get_current_user_id()
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT * FROM payment_methods WHERE user_id = :user_id ORDER BY id"),
            {"user_id": current_user_id},
        )
        rows = result.fetchall()
        methods = []
        for row in rows:
            data = dict(row._mapping)

            def format_dt(dt_val):
                if dt_val is None:
                    return None
                if isinstance(dt_val, datetime):
                    return dt_val.isoformat()
                return str(dt_val)

            method = PaymentMethodResponse(
                id=data["id"],
                user_id=data["user_id"],
                card_type=data["card_type"],
                last_four=data["last_four"],
                expiry_month=data["expiry_month"],
                expiry_year=data["expiry_year"],
                is_default=data["is_default"],
                created_at=format_dt(data["created_at"]),
                updated_at=format_dt(data["updated_at"]),
            )
            methods.append(method)
        return methods


@app.post("/payments", response_model=PaymentResponse, operation_id="process_payment")
def process_payment(
    invoice_id: int = Query(..., description="ID of the invoice to pay"),
    payment_method_id: int = Query(..., description="ID of the payment method to use")
):
    """
    Process payment for an invoice using a saved payment method.
    """
    current_user_id = get_current_user_id()

    with engine.connect() as conn:
        # Verify invoice exists and belongs to the current user
        inv_result = conn.execute(
            text("SELECT * FROM invoices WHERE id = :id AND buyer_id = :user_id"),
            {"id": invoice_id, "user_id": current_user_id},
        )
        invoice_row = inv_result.fetchone()
        if not invoice_row:
            raise HTTPException(status_code=404, detail="Invoice not found or does not belong to current user")

        invoice_data = dict(invoice_row._mapping)
        
        # Check if invoice is already paid
        if invoice_data["status"] != "unpaid":
            raise HTTPException(status_code=400, detail=f"Invoice is already {invoice_data['status']}")

        total_amount = float(invoice_data["total_amount"])

        # Verify payment method exists and belongs to the current user
        pm_result = conn.execute(
            text("SELECT * FROM payment_methods WHERE id = :id AND user_id = :user_id"),
            {"id": payment_method_id, "user_id": current_user_id},
        )
        pm_row = pm_result.fetchone()
        if not pm_row:
            raise HTTPException(status_code=404, detail="Payment method not found or does not belong to current user")

        payment_method_data = dict(pm_row._mapping)

        # Generate transaction ID
        now = datetime.now(timezone.utc)
        txn_id = f"TXN-{now.strftime('%Y%m%d')}-{os.urandom(4).hex().upper()}"

        # Insert payment record
        insert_result = conn.execute(
            text(
                """
                INSERT INTO payments (invoice_id, user_id, payment_method_id, amount, status, transaction_id, paid_at, created_at, updated_at)
                VALUES (:invoice_id, :user_id, :payment_method_id, :amount, :status, :transaction_id, :paid_at, :created_at, :updated_at)
                """
            ),
            {
                "invoice_id": invoice_id,
                "user_id": current_user_id,
                "payment_method_id": payment_method_id,
                "amount": total_amount,
                "status": "completed",
                "transaction_id": txn_id,
                "paid_at": now.isoformat(),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )
        payment_id = insert_result.lastrowid

        # Update invoice status to paid
        conn.execute(
            text(
                "UPDATE invoices SET status = 'paid', paid_at = :paid_at, updated_at = :updated_at WHERE id = :id"
            ),
            {"paid_at": now.isoformat(), "updated_at": now.isoformat(), "id": invoice_id},
        )

        conn.commit()

        # Build response
        payment_response = PaymentResponse(
            id=payment_id,
            invoice_id=invoice_id,
            user_id=current_user_id,
            payment_method_id=payment_method_id,
            amount=total_amount,
            status="completed",
            transaction_id=txn_id,
            paid_at=now.isoformat(),
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        return payment_response
