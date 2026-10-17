import os
from typing import List

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CargoChain Freight Audit Suite")


class DuplicateInvoiceItem(BaseModel):
    duplicate_invoice_id: int
    original_invoice_id: int
    carrier_scac_code: str
    invoice_number: str
    total_amount: float


class FlagDuplicateInvoicesResponse(BaseModel):
    flagged_duplicates: List[DuplicateInvoiceItem]


@app.post("/audit/duplicate-invoices", response_model=FlagDuplicateInvoicesResponse, operation_id="flag_duplicate_invoices")
def flag_duplicate_invoices(
    batch_code: str = Query(..., description="The batch code of the AP batch to scan for duplicates (e.g., 'AP-2024-Q3-07')"),
):
    """
    Identify and flag duplicate invoices in a specified AP batch.
    
    Scans invoices within the given AP batch for duplicates based on carrier SCAC code, 
    invoice number, and total amount. Flags each duplicate with status 'DUPLICATE_DETECTED' 
    and links it to the original invoice using the 'duplicate_of' reference.
    """
    
    # Find groups of invoices that share the same carrier_id, invoice_number, and total_amount
    # within the specific batch. The "original" is the one with the lowest ID.
    query_sql = text("""
        SELECT 
            i.id as invoice_id,
            i.carrier_id,
            c.scac_code,
            i.invoice_number,
            i.total_amount,
            MIN(i.id) OVER (PARTITION BY i.carrier_id, i.invoice_number, i.total_amount) as min_id
        FROM invoices i
        JOIN carriers c ON i.carrier_id = c.id
        JOIN ap_batches ab ON i.batch_id = ab.id
        WHERE ab.batch_code = :batch_code
        ORDER BY i.id ASC
    """)
    
    results = []
    
    with engine.connect() as conn:
        rows = conn.execute(query_sql, {"batch_code": batch_code}).fetchall()
        
        updates_to_perform = []
        
        for row in rows:
            invoice_id = row.invoice_id
            scac_code = row.scac_code
            invoice_number = row.invoice_number
            total_amount = row.total_amount
            original_id = row.min_id
            
            # Only process if this is a duplicate (not the original itself)
            if invoice_id != original_id:
                updates_to_perform.append({
                    "id": invoice_id,
                    "status": "DUPLICATE_DETECTED",
                    "duplicate_of": original_id
                })
                
                results.append(DuplicateInvoiceItem(
                    duplicate_invoice_id=invoice_id,
                    original_invoice_id=original_id,
                    carrier_scac_code=scac_code,
                    invoice_number=invoice_number,
                    total_amount=float(total_amount)
                ))
        
        # Perform updates in a transaction
        if updates_to_perform:
            update_stmt = text("""
                UPDATE invoices 
                SET status = :status, 
                    duplicate_of = :duplicate_of,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """)
            
            for upd in updates_to_perform:
                conn.execute(update_stmt, {
                    "id": upd["id"],
                    "status": upd["status"],
                    "duplicate_of": upd["duplicate_of"]
                })
            
            conn.commit()
            
    return FlagDuplicateInvoicesResponse(flagged_duplicates=results)
