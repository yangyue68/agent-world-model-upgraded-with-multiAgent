import os
from datetime import date, datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Assuming DATABASE_PATH is provided in the environment or context as per instructions
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()

class InvoiceDetail(BaseModel):
    invoice_id: int
    invoice_number: str
    amount_due: float

class StatementResponse(BaseModel):
    statement_id: int
    patient_id: int
    statement_date: str
    total_due: float
    status: str
    invoices: List[InvoiceDetail]

class EmailResponse(BaseModel):
    email_id: int
    statement_id: int
    recipient_email: str
    subject: str
    status: str


@app.post("/patients/{patient_id}/statements", response_model=StatementResponse, operation_id="create_patient_statement")
def create_patient_statement(patient_id: int = Path(..., description="Internal ID of the patient")):
    """
    Creates a new statement for a specified patient with outstanding balances from unpaid or partial invoices.
    """
    with engine.connect() as conn:
        # 1. Verify patient exists
        patient_result = conn.execute(
            text("SELECT id, first_name, last_name, email FROM patients WHERE id = :pid"),
            {"pid": patient_id}
        ).fetchone()
        
        if not patient_result:
            raise HTTPException(status_code=404, detail="Patient not found")
        
        patient_first_name, patient_last_name, patient_email = patient_result[1], patient_result[2], patient_result[3]

        # 2. Find unpaid or partial invoices for this patient
        invoices_result = conn.execute(
            text("""
                SELECT id, invoice_number, (total_amount - amount_paid) as amount_due 
                FROM invoices 
                WHERE patient_id = :pid AND status IN ('unpaid', 'partial')
            """),
            {"pid": patient_id}
        ).fetchall()

        if not invoices_result:
            raise HTTPException(status_code=400, detail="No outstanding invoices found for this patient")

        # Calculate total due and prepare invoice details
        total_due = sum(inv.amount_due for inv in invoices_result)
        invoice_details = [
            InvoiceDetail(
                invoice_id=inv.id,
                invoice_number=inv.invoice_number,
                amount_due=inv.amount_due
            )
            for inv in invoices_result
        ]

        # 3. Create the statement record
        today = date.today().isoformat()
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        # The schema doesn't have created_by on statements, but emails do. 
        # We just insert into statements.
        
        stmt_insert = conn.execute(
            text("""
                INSERT INTO statements (patient_id, statement_date, total_due, status, notes)
                VALUES (:pid, :sdate, :tdue, :status, :notes)
            """),
            {
                "pid": patient_id,
                "sdate": today,
                "tdue": total_due,
                "status": "draft",
                "notes": f"Auto-generated statement for outstanding balance"
            }
        )
        statement_id = stmt_insert.lastrowid

        # 4. Link invoices to the statement
        for inv in invoices_result:
            conn.execute(
                text("""
                    INSERT INTO statement_invoices (statement_id, invoice_id, amount_due)
                    VALUES (:sid, :iid, :amount)
                """),
                {
                    "sid": statement_id,
                    "iid": inv.id,
                    "amount": inv.amount_due
                }
            )
        
        conn.commit()

        return StatementResponse(
            statement_id=statement_id,
            patient_id=patient_id,
            statement_date=today,
            total_due=total_due,
            status="draft",
            invoices=invoice_details
        )


@app.post("/statements/{statement_id}/send-email", response_model=EmailResponse, operation_id="send_statement_via_email")
def send_statement_via_email(statement_id: int = Path(..., description="Internal ID of the statement to be sent")):
    """
    Sends the specified statement to the patient's email address using the system's email service.
    Updates the statement status to 'sent' upon successful delivery.
    """
    with engine.connect() as conn:
        # 1. Get statement details and patient info
        stmt_result = conn.execute(
            text("""
                SELECT s.id, s.patient_id, s.total_due, p.first_name, p.last_name, p.email
                FROM statements s
                JOIN patients p ON s.patient_id = p.id
                WHERE s.id = :sid
            """),
            {"sid": statement_id}
        ).fetchone()

        if not stmt_result:
            raise HTTPException(status_code=404, detail="Statement not found")
        
        _, patient_id, total_due, first_name, last_name, recipient_email = stmt_result
        
        if not recipient_email:
            raise HTTPException(status_code=400, detail="Patient does not have an email address")

        # 2. Prepare email content
        subject = f"Your ClinicFlow Statement - {datetime.now().strftime('%B %Y')}"
        body = f"Dear {first_name} {last_name}, please find your statement attached. Total due: ${total_due:.2f}."

        # 3. Determine sender user ID from environment variable
        sender_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not sender_user_id_str:
            # Fallback or error depending on strictness, assuming env var is present as per prompt
            raise HTTPException(status_code=500, detail="Current user context missing")
        
        try:
            sender_user_id = int(sender_user_id_str)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid current user context")

        # Verify sender user exists (optional but good practice given FK constraint)
        user_exists = conn.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": sender_user_id}
        ).fetchone()
        if not user_exists:
             raise HTTPException(status_code=400, detail="Sender user not found")

        # 4. Insert email record
        now = datetime.now().isoformat(sep=' ', timespec='seconds')
        
        email_insert = conn.execute(
            text("""
                INSERT INTO emails (sender_user_id, recipient_email, subject, body, status, sent_at, related_statement_id)
                VALUES (:sender, :recipient, :subject, :body, :status, :sent_at, :stmt_id)
            """),
            {
                "sender": sender_user_id,
                "recipient": recipient_email,
                "subject": subject,
                "body": body,
                "status": "sent",
                "sent_at": now,
                "stmt_id": statement_id
            }
        )
        email_id = email_insert.lastrowid

        # 5. Update statement status to 'sent'
        conn.execute(
            text("UPDATE statements SET status = 'sent', updated_at = :now WHERE id = :sid"),
            {"now": now, "sid": statement_id}
        )

        conn.commit()

        return EmailResponse(
            email_id=email_id,
            statement_id=statement_id,
            recipient_email=recipient_email,
            subject=subject,
            status="sent"
        )
