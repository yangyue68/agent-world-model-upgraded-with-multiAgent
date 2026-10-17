import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class LateFeeRequest(BaseModel):
    amount: float
    reason: Optional[str] = None


class PaymentRequest(BaseModel):
    amount: float
    payment_method: str
    reference_number: Optional[str] = None
    notes: Optional[str] = None


@app.post("/invoices/{invoice_id}/late_fee", operation_id="apply_late_fee")
def apply_late_fee(invoice_id: int, body: LateFeeRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        applied_by = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, status FROM invoices WHERE id = :iid"),
            {"iid": invoice_id}
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if row.status != "overdue":
            raise HTTPException(status_code=400, detail="Invoice must be overdue to apply a late fee")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        result = conn.execute(
            text("""
                INSERT INTO invoice_adjustments (invoice_id, adjustment_type, amount, reason, applied_by, created_at)
                VALUES (:iid, 'late_fee', :amt, :reason, :applied_by, :now)
            """),
            {
                "iid": invoice_id,
                "amt": body.amount,
                "reason": body.reason,
                "applied_by": applied_by,
                "now": now
            }
        )
        adj_id = result.lastrowid

        new_row = conn.execute(
            text("""
                SELECT id, invoice_id, adjustment_type, amount, reason, applied_by, created_at
                FROM invoice_adjustments WHERE id = :aid
            """),
            {"aid": adj_id}
        ).fetchone()

        return {
            "id": new_row.id,
            "invoice_id": new_row.invoice_id,
            "adjustment_type": new_row.adjustment_type,
            "amount": new_row.amount,
            "reason": new_row.reason,
            "applied_by": new_row.applied_by,
            "created_at": new_row.created_at
        }


@app.post("/invoices/{invoice_id}/payments", operation_id="record_partial_payment")
def record_partial_payment(invoice_id: int, body: PaymentRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        recorded_by = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    valid_methods = {'cash', 'credit_card', 'debit_card', 'check', 'bank_transfer', 'insurance', 'other'}
    if body.payment_method not in valid_methods:
        raise HTTPException(status_code=422, detail=f"Invalid payment method. Must be one of: {valid_methods}")

    with engine.begin() as conn:
        inv = conn.execute(
            text("SELECT id, balance, total_amount, amount_paid FROM invoices WHERE id = :iid"),
            {"iid": invoice_id}
        ).fetchone()

        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if body.amount <= 0:
            raise HTTPException(status_code=400, detail="Payment amount must be positive")

        if body.amount > inv.balance:
            raise HTTPException(status_code=400, detail="Payment amount exceeds outstanding balance")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        result = conn.execute(
            text("""
                INSERT INTO payments (invoice_id, amount, payment_method, payment_date, reference_number, notes, recorded_by, created_at)
                VALUES (:iid, :amt, :pm, :pd, :ref, :notes, :rb, :ca)
            """),
            {
                "iid": invoice_id,
                "amt": body.amount,
                "pm": body.payment_method,
                "pd": now,
                "ref": body.reference_number,
                "notes": body.notes,
                "rb": recorded_by,
                "ca": now
            }
        )
        pay_id = result.lastrowid

        new_balance = round(inv.balance - body.amount, 2)
        new_amount_paid = round(inv.amount_paid + body.amount, 2)

        if new_balance == 0:
            new_status = "paid"
        elif new_amount_paid > 0:
            new_status = "partial"
        else:
            new_status = "unpaid"

        conn.execute(
            text("""
                UPDATE invoices 
                SET amount_paid = :ap, balance = :bal, status = :st, updated_at = :ua
                WHERE id = :iid
            """),
            {
                "ap": new_amount_paid,
                "bal": new_balance,
                "st": new_status,
                "ua": now,
                "iid": invoice_id
            }
        )

        p_row = conn.execute(
            text("""
                SELECT id, invoice_id, amount, payment_method, payment_date, reference_number, notes, recorded_by, created_at
                FROM payments WHERE id = :pid
            """),
            {"pid": pay_id}
        ).fetchone()

        return {
            "id": p_row.id,
            "invoice_id": p_row.invoice_id,
            "amount": p_row.amount,
            "payment_method": p_row.payment_method,
            "payment_date": p_row.payment_date,
            "reference_number": p_row.reference_number,
            "notes": p_row.notes,
            "recorded_by": p_row.recorded_by,
            "created_at": p_row.created_at
        }
