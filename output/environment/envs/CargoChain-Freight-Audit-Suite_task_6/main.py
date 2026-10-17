import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CargoChain Freight Audit Suite")


# --- Pydantic Models for Responses and Requests ---

class InvoiceDetail(BaseModel):
    id: int
    invoice_number: str
    amount: float
    origin_zip: str
    weight: float


class InvoicePairResponse(BaseModel):
    invoice_1: InvoiceDetail
    invoice_2: InvoiceDetail


class CrossBatchDuplicateCheckResponseItem(BaseModel):
    invoice_pair: InvoicePairResponse


class InvoicePairInput(BaseModel):
    invoice_id_1: int
    invoice_id_2: int


class ClassifyDuplicatesRequest(BaseModel):
    invoice_pairs: List[InvoicePairInput]
    created_by: int 


class AuditExceptionRecord(BaseModel):
    id: int
    exception_type_id: int
    invoice_id_1: int
    invoice_id_2: int
    classification: str
    details: Optional[str] = None
    created_by: int


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/audit/cross_batch_duplicate_check", response_model=List[CrossBatchDuplicateCheckResponseItem], operation_id="run_cross_batch_duplicate_check")
async def run_cross_batch_duplicate_check(
    payment_run_code_1: str = Query(..., description="First payment run code"),
    payment_run_code_2: str = Query(..., description="Second payment run code"),
    carrier_code: str = Query(..., description="Carrier code")
):
    """
    Identifies potential fraud duplicates by comparing invoices from two different payment runs 
    for the same carrier. Matches are based on invoice number and amount, but origin ZIP and weight must differ.
    """
    db = SessionLocal()
    try:
        # 1. Get Carrier ID
        stmt_carrier = text("""
            SELECT id FROM carriers WHERE carrier_code = :carrier_code
        """)
        res_carrier = db.execute(stmt_carrier, {"carrier_code": carrier_code})
        carrier_row = res_carrier.fetchone()
        
        if not carrier_row:
            return []
            
        carrier_id = carrier_row[0]

        # 2. Get Payment Run IDs
        stmt_runs = text("""
            SELECT id, run_code FROM payment_runs WHERE run_code IN (:run1, :run2)
        """)
        res_runs = db.execute(stmt_runs, {"run1": payment_run_code_1, "run2": payment_run_code_2})
        run_rows = res_runs.fetchall()
        
        run_map = {row[1]: row[0] for row in run_rows}
        
        pr_id_1 = run_map.get(payment_run_code_1)
        pr_id_2 = run_map.get(payment_run_code_2)
        
        if not pr_id_1 or not pr_id_2:
            return []

        # 3. Find matching invoices across the two runs for this carrier
        # Condition: Same invoice_number, Same amount, Different origin_zip OR Different weight
        # We join invoices i1 (from run 1) and i2 (from run 2)
        
        query_sql = text("""
            SELECT 
                i1.id as i1_id, i1.invoice_number as i1_inv_num, i1.amount as i1_amt, i1.origin_zip as i1_zip, i1.weight as i1_wt,
                i2.id as i2_id, i2.invoice_number as i2_inv_num, i2.amount as i2_amt, i2.origin_zip as i2_zip, i2.weight as i2_wt
            FROM invoices i1
            JOIN invoices i2 ON i1.invoice_number = i2.invoice_number AND i1.amount = i2.amount
            WHERE i1.carrier_id = :carrier_id
              AND i2.carrier_id = :carrier_id
              AND i1.payment_run_id = :pr_id_1
              AND i2.payment_run_id = :pr_id_2
              AND (i1.origin_zip != i2.origin_zip OR i1.weight != i2.weight)
        """)
        
        params = {
            "carrier_id": carrier_id,
            "pr_id_1": pr_id_1,
            "pr_id_2": pr_id_2
        }
        
        results = db.execute(query_sql, params).fetchall()
        
        response_items = []
        for row in results:
            inv1 = InvoiceDetail(
                id=row.i1_id,
                invoice_number=row.i1_inv_num,
                amount=float(row.i1_amt),
                origin_zip=row.i1_zip,
                weight=float(row.i1_wt)
            )
            inv2 = InvoiceDetail(
                id=row.i2_id,
                invoice_number=row.i2_inv_num,
                amount=float(row.i2_amt),
                origin_zip=row.i2_zip,
                weight=float(row.i2_wt)
            )
            
            pair_resp = InvoicePairResponse(invoice_1=inv1, invoice_2=inv2)
            item = CrossBatchDuplicateCheckResponseItem(invoice_pair=pair_resp)
            response_items.append(item)
            
        return response_items
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/audit/classify_duplicates", response_model=List[AuditExceptionRecord], operation_id="classify_potential_fraud_duplicates")
async def classify_potential_fraud_duplicates(request_body: ClassifyDuplicatesRequest):
    """
    Creates audit exception records for identified potential fraud duplicates.
    Assigns exception type 'INCONSISTENT_SHIPMENT_CONTEXT'.
    """
    db = SessionLocal()
    try:
        # Get current user ID from environment
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id_str:
             # Fallback or error? Instructions say read env var. 
             # If missing, we can't proceed securely. 
             # Assuming runtime guarantees it exists for writes.
             raise HTTPException(status_code=401, detail="User context missing")
             
        current_user_id = int(current_user_id_str)

        # Get Exception Type ID for 'INCONSISTENT_SHIPMENT_CONTEXT'
        stmt_exc_type = text("""
            SELECT id FROM exception_types WHERE type_code = 'INCONSISTENT_SHIPMENT_CONTEXT'
        """)
        res_exc_type = db.execute(stmt_exc_type)
        exc_type_row = res_exc_type.fetchone()
        
        if not exc_type_row:
            raise HTTPException(status_code=404, detail="Exception type INCONSISTENT_SHIPMENT_CONTEXT not found")
            
        exception_type_id = exc_type_row[0]

        created_records = []
        
        for pair in request_body.invoice_pairs:
            inv_id_1 = pair.invoice_id_1
            inv_id_2 = pair.invoice_id_2
            
            # Ensure consistent ordering for CHECK constraint (invoice_id_1 < invoice_id_2)
            final_id_1 = min(inv_id_1, inv_id_2)
            final_id_2 = max(inv_id_1, inv_id_2)
            
            # Verify invoices exist and belong to valid state? 
            # The spec doesn't ask for verification, just creation. 
            # But FK constraints will handle existence.
            
            insert_stmt = text("""
                INSERT INTO audit_exceptions (
                    exception_type_id, 
                    invoice_id_1, 
                    invoice_id_2, 
                    classification, 
                    details, 
                    created_by
                ) VALUES (
                    :exc_type_id, 
                    :inv_1, 
                    :inv_2, 
                    :classification, 
                    :details, 
                    :created_by
                ) RETURNING id
            """)
            
            params = {
                "exc_type_id": exception_type_id,
                "inv_1": final_id_1,
                "inv_2": final_id_2,
                "classification": "potential fraud duplicate",
                "details": f"Invoices {final_id_1} and {final_id_2} matched on number/amount but differed in shipment context.",
                "created_by": current_user_id
            }
            
            result = db.execute(insert_stmt, params)
            new_id = result.fetchone()[0]
            db.commit()
            
            record = AuditExceptionRecord(
                id=new_id,
                exception_type_id=exception_type_id,
                invoice_id_1=final_id_1,
                invoice_id_2=final_id_2,
                classification="potential fraud duplicate",
                details=params["details"],
                created_by=current_user_id
            )
            created_records.append(record)
            
        return created_records

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
