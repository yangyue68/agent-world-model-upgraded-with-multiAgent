import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class InvoiceResponse(BaseModel):
    id: int
    invoice_number: str
    patient_id: int
    issue_date: str
    due_date: Optional[str] = None
    total_amount: float
    amount_paid: float
    balance: float
    status: str
    notes: Optional[str] = None
    created_at: str
    updated_at: str


class PaymentCreateRequest(BaseModel):
    invoice_id: int
    patient_id: int
    amount: float
    payment_method: str = Field(..., pattern="^(cash|credit_card|debit_card|check|bank_transfer|insurance|other)$")
    reference_number: Optional[str] = None
    notes: Optional[str] = None


class PaymentResponse(BaseModel):
    id: int
    invoice_id: int
    patient_id: int
    amount: float
    payment_method: str
    payment_date: str
    reference_number: Optional[str] = None
    notes: Optional[str] = None
    created_by: Optional[int] = None
    created_at: str
    updated_at: str


@app.get("/invoices/{invoice_number}", response_model=InvoiceResponse, operation_id="get_invoice_by_number")
def get_invoice_by_number(invoice_number: str = Path(...)):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, invoice_number, patient_id, issue_date, due_date, 
                       total_amount, amount_paid, balance, status, notes, 
                       created_at, updated_at
                FROM invoices
                WHERE invoice_number = :invoice_number
            """),
            {"invoice_number": invoice_number}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Invoice not found")
        
        return InvoiceResponse(
            id=result[0],
            invoice_number=result[1],
            patient_id=result[2],
            issue_date=result[3],
            due_date=result[4],
            total_amount=float(result[5]),
            amount_paid=float(result[6]),
            balance=float(result[7]),
            status=result[8],
            notes=result[9],
            created_at=str(result[10]),
            updated_at=str(result[11])
        )


@app.post("/payments", response_model=PaymentResponse, operation_id="create_payment")
def create_payment(payment_data: PaymentCreateRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    current_user_id = int(current_user_id_str) if current_user_id_str else None
    
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    with engine.begin() as conn:
        # Get the invoice to validate and update
        invoice_result = conn.execute(
            text("""
                SELECT id, patient_id, total_amount, amount_paid, balance, status
                FROM invoices
                WHERE id = :invoice_id
            """),
            {"invoice_id": payment_data.invoice_id}
        ).fetchone()
        
        if not invoice_result:
            raise HTTPException(status_code=404, detail="Invoice not found")
        
        inv_id, inv_patient_id, total_amount, amount_paid, balance, status = invoice_result
        
        # Validate patient ID matches
        if inv_patient_id != payment_data.patient_id:
            raise HTTPException(status_code=400, detail="Patient ID does not match invoice")
        
        # Validate payment amount doesn't exceed balance
        if payment_data.amount > balance:
            raise HTTPException(status_code=400, detail="Payment amount exceeds outstanding balance")
        
        # Insert payment record
        result = conn.execute(
            text("""
                INSERT INTO payments (invoice_id, patient_id, amount, payment_method, 
                                      payment_date, reference_number, notes, created_by, 
                                      created_at, updated_at)
                VALUES (:invoice_id, :patient_id, :amount, :payment_method, 
                        :payment_date, :reference_number, :notes, :created_by, 
                        :created_at, :updated_at)
            """),
            {
                "invoice_id": payment_data.invoice_id,
                "patient_id": payment_data.patient_id,
                "amount": payment_data.amount,
                "payment_method": payment_data.payment_method,
                "payment_date": now.split()[0],  # Just the date part
                "reference_number": payment_data.reference_number,
                "notes": payment_data.notes,
                "created_by": current_user_id,
                "created_at": now,
                "updated_at": now
            }
        )
        
        payment_id = result.lastrowid
        
        # Update invoice amounts
        new_amount_paid = amount_paid + payment_data.amount
        new_balance = balance - payment_data.amount
        
        # Determine new status
        if new_balance <= 0:
            new_status = "paid"
        elif new_amount_paid > 0:
            new_status = "partial"
        else:
            new_status = status
        
        conn.execute(
            text("""
                UPDATE invoices
                SET amount_paid = :amount_paid,
                    balance = :balance,
                    status = :status,
                    updated_at = :updated_at
                WHERE id = :invoice_id
            """),
            {
                "amount_paid": new_amount_paid,
                "balance": new_balance,
                "status": new_status,
                "updated_at": now,
                "invoice_id": payment_data.invoice_id
            }
        )
        
        # Fetch the created payment for response
        payment_result = conn.execute(
            text("""
                SELECT id, invoice_id, patient_id, amount, payment_method, 
                       payment_date, reference_number, notes, created_by, 
                       created_at, updated_at
                FROM payments
                WHERE id = :payment_id
            """),
            {"payment_id": payment_id}
        ).fetchone()
        
        return PaymentResponse(
            id=payment_result[0],
            invoice_id=payment_result[1],
            patient_id=payment_result[2],
            amount=float(payment_result[3]),
            payment_method=payment_result[4],
            payment_date=payment_result[5],
            reference_number=payment_result[6],
            notes=payment_result[7],
            created_by=payment_result[8],
            created_at=str(payment_result[9]),
            updated_at=str(payment_result[10])
        )
