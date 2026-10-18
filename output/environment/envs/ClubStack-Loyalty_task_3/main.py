import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class InvoiceResponse(BaseModel):
    id: int
    invoice_number: str
    amount: float
    currency: str
    status: str
    issued_at: str
    due_at: Optional[str]
    payment_status: str


@app.get("/user/invoices", response_model=List[InvoiceResponse], operation_id="get_user_invoices")
def get_user_invoices():
    """
    Retrieve the last 5 invoices for the current user.
    Includes associated payment status.
    Ordered by issued date descending.
    """
    # Get current user ID from environment variable injected by runtime
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    db = SessionLocal()
    try:
        # Query to fetch the last 5 invoices for the user, joined with payments to get payment status.
        # We need to handle cases where an invoice might have multiple payments or no payments.
        # Based on the schema and typical usage, we assume one primary payment per invoice or 
        # we want the latest/most relevant payment status. 
        # The spec says "associated payment status". Looking at the data, each paid invoice has one successful payment.
        # Pending invoices might have pending payments or none.
        
        # To ensure we get the correct payment status even if there are multiple payments (though unlikely in this simple model),
        # we can join and pick the most recent payment or just any payment linked. 
        # However, a simpler approach given the 1-to-1 nature often seen in these examples is a LEFT JOIN.
        # If there are multiple payments, which one determines the status? Usually the latest one.
        # Let's use a subquery or window function if needed, but standard SQL with GROUP BY or DISTINCT ON isn't always straightforward in SQLite without specific versions.
        # Given the constraints and simplicity, let's assume we want the status of the payment associated with the invoice.
        # If an invoice has no payment, status should probably be 'pending' or similar based on invoice status?
        # The API response requires `payment_status`.
        
        # Let's refine the query:
        # Select invoice fields and the status of the corresponding payment.
        # Order by issued_at DESC, limit 5.
        
        sql_query = text("""
            SELECT 
                i.id,
                i.invoice_number,
                i.amount,
                i.currency,
                i.status as invoice_status,
                i.issued_at,
                i.due_at,
                COALESCE(p.status, 'no_payment') as payment_status
            FROM invoices i
            LEFT JOIN payments p ON i.id = p.invoice_id
            WHERE i.user_id = :user_id
            ORDER BY i.issued_at DESC
            LIMIT 5
        """)
        
        result = db.execute(sql_query, {"user_id": current_user_id})
        rows = result.fetchall()
        
        invoices_list = []
        for row in rows:
            # Format datetime strings properly if they come back as objects, though SQLAlchemy usually returns them as strings or datetime objects depending on driver/config.
            # Assuming standard string representation from DB or converting datetime objects.
            
            issued_at_val = row.issued_at
            due_at_val = row.due_at
            
            # Ensure consistent formatting if necessary, but typically returning ISO format strings is good.
            # If they are datetime objects, convert to isoformat.
            if hasattr(issued_at_val, 'isoformat'):
                issued_at_str = issued_at_val.isoformat(sep=' ')
            else:
                issued_at_str = str(issued_at_val)
                
            if due_at_val is None:
                due_at_str = None
            elif hasattr(due_at_val, 'isoformat'):
                due_at_str = due_at_val.isoformat(sep=' ')
            else:
                due_at_str = str(due_at_val)

            inv_obj = InvoiceResponse(
                id=row.id,
                invoice_number=row.invoice_number,
                amount=float(row.amount),
                currency=row.currency,
                status=row.invoice_status,
                issued_at=issued_at_str,
                due_at=due_at_str,
                payment_status=row.payment_status
            )
            invoices_list.append(inv_obj)
            
        return invoices_list
        
    finally:
        db.close()
