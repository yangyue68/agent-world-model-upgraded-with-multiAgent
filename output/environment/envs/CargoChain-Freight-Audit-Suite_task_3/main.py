import os
from datetime import datetime, timedelta

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, Literal
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CargoChain Freight Audit Suite")


class DuplicateCheckResponse(BaseModel):
    status: Literal["DUPLICATE_DETECTED", "NO_DUPLICATE_FOUND"]
    original_audit_timestamp: Optional[str] = None
    original_shipment_id: Optional[str] = None


@app.post("/audit/invoice_duplicate_check", response_model=DuplicateCheckResponse, operation_id="check_invoice_duplicate")
def check_invoice_duplicate(
    carrier_scac: str = Query(..., description="SCAC code of the carrier (e.g., TRLK)"),
    invoice_number: str = Query(..., description="Invoice number to check for duplicates (e.g., TLK-88442)"),
    amount: float = Query(..., description="Invoice amount to check for duplicates ($1,847.33)")
):
    """
    Detect if an invoice with identical SCAC, invoice number, and amount exists 
    for the same carrier within the last 90 days. Returns DUPLICATE_DETECTED 
    with original audit timestamp and shipment ID if found.
    """
    
    # Calculate the cutoff date for the last 90 days
    now = datetime.utcnow()
    cutoff_date = now - timedelta(days=90)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")

    query_sql = text("""
        SELECT i.audit_timestamp, s.shipment_id
        FROM invoices i
        JOIN carriers c ON i.carrier_id = c.id
        LEFT JOIN shipments s ON i.shipment_id = s.id
        WHERE c.scac = :scac
          AND i.invoice_number = :invoice_number
          AND ABS(i.amount - :amount) < 0.01
          AND i.audit_timestamp >= :cutoff_date
        ORDER BY i.audit_timestamp ASC
        LIMIT 1
    """)

    params = {
        "scac": carrier_scac,
        "invoice_number": invoice_number,
        "amount": amount,
        "cutoff_date": cutoff_str
    }

    try:
        with engine.connect() as conn:
            result = conn.execute(query_sql, params).fetchone()
            
            if result:
                audit_ts, shipment_id = result
                
                # Format timestamp to ISO 8601 string if needed, though DB returns string usually
                # The spec asks for date-time format. SQLite DATETIME is typically 'YYYY-MM-DD HH:MM:SS'
                # We will return it as-is or ensure it's a valid string representation.
                
                return DuplicateCheckResponse(
                    status="DUPLICATE_DETECTED",
                    original_audit_timestamp=str(audit_ts),
                    original_shipment_id=str(shipment_id) if shipment_id else None
                )
            else:
                return DuplicateCheckResponse(
                    status="NO_DUPLICATE_FOUND"
                )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
