import os
from datetime import date, datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CareCircle Clinic Manager API")


class ServiceCodeItem(BaseModel):
    service_code_id: int
    quantity: int = 1


class CreateInvoiceRequest(BaseModel):
    visit_id: int
    service_codes: List[ServiceCodeItem]
    discount_percent: float = 0.0


class InvoiceResponse(BaseModel):
    invoice_id: int
    status: str
    total: float


class RecordPaymentRequest(BaseModel):
    invoice_id: int
    amount: float
    payment_method: str
    payment_date: date


class PaymentResponse(BaseModel):
    payment_id: int
    invoice_status: str


@app.post("/invoices", response_model=InvoiceResponse, operation_id="create_invoice")
def create_invoice(request: CreateInvoiceRequest):
    db = SessionLocal()
    try:
        # Validate Visit exists and get patient_id
        visit_row = db.execute(
            text("SELECT id, patient_id FROM visits WHERE id = :visit_id"),
            {"visit_id": request.visit_id}
        ).fetchone()

        if not visit_row:
            raise HTTPException(status_code=404, detail="Visit not found")

        visit_id = visit_row.id
        patient_id = visit_row.patient_id

        # Fetch service codes details to calculate subtotal
        service_ids = [item.service_code_id for item in request.service_codes]
        
        # We need to map requested quantities to prices from DB
        placeholders = ", ".join([f":sid_{i}" for i in range(len(service_ids))])
        params = {f"sid_{i}": sid for i, sid in enumerate(service_ids)}
        
        query_str = f"""
            SELECT id, description, default_price 
            FROM service_codes 
            WHERE id IN ({placeholders})
        """
        sc_rows = db.execute(text(query_str), params).fetchall()
        sc_map = {row.id: row for row in sc_rows}

        subtotal = 0.0
        items_to_insert = []

        for req_item in request.service_codes:
            sc = sc_map.get(req_item.service_code_id)
            if not sc:
                raise HTTPException(status_code=404, detail=f"Service code ID {req_item.service_code_id} not found")
            
            qty = req_item.quantity
            price = sc.default_price
            line_total = round(price * qty, 2)
            subtotal += line_total
            
            items_to_insert.append({
                "service_code_id": sc.id,
                "description": sc.description,
                "quantity": qty,
                "unit_price": price,
                "line_total": line_total
            })

        subtotal = round(subtotal, 2)

        # Calculate Discount
        discount_percent = request.discount_percent
        if discount_percent < 0 or discount_percent > 100:
             raise HTTPException(status_code=400, detail="Discount percent must be between 0 and 100")
             
        discount_amount = round(subtotal * (discount_percent / 100.0), 2)
        total = round(subtotal - discount_amount, 2)

        # Get current user for created_by
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        created_by = int(current_user_id_str) if current_user_id_str else None

        today = date.today()

        # Insert Invoice
        insert_inv_sql = text("""
            INSERT INTO invoices (visit_id, patient_id, invoice_date, subtotal, discount_percent, discount_amount, total, status, created_by)
            VALUES (:visit_id, :patient_id, :invoice_date, :subtotal, :discount_percent, :discount_amount, :total, 'unpaid', :created_by)
        """)
        
        res = db.execute(insert_inv_sql, {
            "visit_id": visit_id,
            "patient_id": patient_id,
            "invoice_date": today,
            "subtotal": subtotal,
            "discount_percent": discount_percent,
            "discount_amount": discount_amount,
            "total": total,
            "created_by": created_by
        })
        
        invoice_id = res.lastrowid
        
        # Insert Invoice Items
        if items_to_insert:
            insert_items_sql = text("""
                INSERT INTO invoice_items (invoice_id, service_code_id, description, quantity, unit_price, line_total)
                VALUES (:invoice_id, :service_code_id, :description, :quantity, :unit_price, :line_total)
            """)
            for item in items_to_insert:
                db.execute(insert_items_sql, {
                    "invoice_id": invoice_id,
                    **item
                })

        db.commit()

        return InvoiceResponse(
            invoice_id=invoice_id,
            status="unpaid",
            total=total
        )

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/payments", response_model=PaymentResponse, operation_id="record_payment")
def record_payment(request: RecordPaymentRequest):
    db = SessionLocal()
    try:
        # Validate Invoice exists
        inv_row = db.execute(
            text("SELECT id, total, status FROM invoices WHERE id = :invoice_id"),
            {"invoice_id": request.invoice_id}
        ).fetchone()

        if not inv_row:
            raise HTTPException(status_code=404, detail="Invoice not found")

        invoice_id = inv_row.id
        invoice_total = inv_row.total
        current_status = inv_row.status

        if current_status == 'void':
            raise HTTPException(status_code=400, detail="Cannot pay a voided invoice")

        # Check valid payment method against schema constraints
        valid_methods = ['cash', 'credit_card', 'debit_card', 'check', 'insurance', 'other']
        if request.payment_method not in valid_methods:
            raise HTTPException(status_code=400, detail=f"Invalid payment method. Must be one of {valid_methods}")

        # Get current user for created_by
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        created_by = int(current_user_id_str) if current_user_id_str else None

        # Insert Payment
        insert_pay_sql = text("""
            INSERT INTO payments (invoice_id, amount, payment_method, payment_date, created_by)
            VALUES (:invoice_id, :amount, :payment_method, :payment_date, :created_by)
        """)
        
        res = db.execute(insert_pay_sql, {
            "invoice_id": invoice_id,
            "amount": request.amount,
            "payment_method": request.payment_method,
            "payment_date": request.payment_date,
            "created_by": created_by
        })
        
        payment_id = res.lastrowid

        # Calculate new status based on sum of payments vs total
        sum_paid_res = db.execute(
            text("SELECT COALESCE(SUM(amount), 0) as total_paid FROM payments WHERE invoice_id = :invoice_id"),
            {"invoice_id": invoice_id}
        ).fetchone()
        
        total_paid = sum_paid_res.total_paid
        
        new_status = "unpaid"
        if total_paid >= invoice_total:
            new_status = "paid"
        elif total_paid > 0:
            new_status = "partial"
        
        # Update Invoice Status
        update_inv_sql = text("""
            UPDATE invoices 
            SET status = :status, updated_at = CURRENT_TIMESTAMP
            WHERE id = :invoice_id
        """)
        db.execute(update_inv_sql, {
            "status": new_status,
            "invoice_id": invoice_id
        })

        db.commit()

        return PaymentResponse(
            payment_id=payment_id,
            invoice_status=new_status
        )

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
