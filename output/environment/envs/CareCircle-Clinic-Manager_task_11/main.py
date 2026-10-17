import os
from datetime import date, datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
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


# --- Pydantic Models for Requests ---

class PaymentRequest(BaseModel):
    amount: float = Field(..., gt=0, description="The payment amount")
    payment_method: str = Field(..., description="The method of payment (cash, check, credit_card, debit_card, insurance, other)")
    payment_date: Optional[str] = Field(None, description="The date of payment (defaults to current date if not provided)")
    reference_number: Optional[str] = Field(None, description="Reference number for the payment")
    notes: Optional[str] = Field(None, description="Additional notes about the payment")


class AdjustmentRequest(BaseModel):
    amount: float = Field(..., gt=0, description="The adjustment amount")
    adjustment_type: str = Field(..., description="The type of adjustment (write_off, discount, courtesy, insurance_adjustment, other)")
    reason: Optional[str] = Field(None, description="Reason for the adjustment")
    adjustment_date: Optional[str] = Field(None, description="The date of adjustment (defaults to current date if not provided)")


# --- Pydantic Models for Responses ---

class PatientResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    date_of_birth: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


class InvoiceDetailsResponse(BaseModel):
    id: int
    invoice_number: str
    patient_id: int
    issue_date: str
    due_date: Optional[str] = None
    total_amount: float
    status: str
    created_at: str
    updated_at: str


class PaymentResponse(BaseModel):
    id: int
    invoice_id: int
    patient_id: int
    amount: float
    payment_date: str
    payment_method: str
    reference_number: Optional[str] = None
    notes: Optional[str] = None
    created_by: int
    created_at: str
    updated_at: str


class AdjustmentResponse(BaseModel):
    id: int
    invoice_id: int
    patient_id: int
    amount: float
    adjustment_type: str
    reason: Optional[str] = None
    adjustment_date: str
    created_by: int
    created_at: str
    updated_at: str


# --- Helper Functions ---

def get_current_user_id() -> int:
    """Reads the simulated logged-in actor from environment variables."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user ID format in environment")


def parse_iso_date(date_str: Optional[str]) -> str:
    """Parses ISO-8601 date strings or returns today's date as string."""
    if date_str is None:
        return date.today().isoformat()
    
    # Handle both 'YYYY-MM-DD' and potential datetime formats like 'YYYY-MM-DDTHH:MM:SSZ'
    # The spec says format=date, but we should be robust.
    clean_date = date_str.split('T')[0].split(' ')[0]
    try:
        # Validate it can be parsed as a date
        datetime.strptime(clean_date, "%Y-%m-%d")
        return clean_date
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str}")


# --- Endpoints ---

@app.get("/patients/search", response_model=List[PatientResponse], operation_id="search_patients")
async def search_patients(
    first_name: Optional[str] = Query(None, description="First name to search for"),
    last_name: Optional[str] = Query(None, description="Last name to search for")
):
    db = SessionLocal()
    try:
        query = "SELECT id, first_name, last_name, date_of_birth, phone, email, address FROM patients WHERE 1=1"
        params = {}
        
        if first_name:
            query += " AND lower(first_name) LIKE :first_name_pattern"
            params["first_name_pattern"] = f"%{first_name.lower()}%"
            
        if last_name:
            query += " AND lower(last_name) LIKE :last_name_pattern"
            params["last_name_pattern"] = f"%{last_name.lower()}%"
            
        result = db.execute(text(query), params).fetchall()
        
        patients = []
        for row in result:
            patients.append(PatientResponse(
                id=row.id,
                first_name=row.first_name,
                last_name=row.last_name,
                date_of_birth=str(row.date_of_birth) if row.date_of_birth else None,
                phone=row.phone,
                email=row.email,
                address=row.address
            ))
        return patients
    finally:
        db.close()


@app.get("/invoices/{invoice_id}", response_model=InvoiceDetailsResponse, operation_id="get_invoice_details")
async def get_invoice_details(invoice_id: int):
    db = SessionLocal()
    try:
        query = """
            SELECT id, invoice_number, patient_id, issue_date, due_date, total_amount, status, created_at, updated_at 
            FROM invoices 
            WHERE id = :invoice_id
        """
        result = db.execute(text(query), {"invoice_id": invoice_id}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Invoice not found")
            
        return InvoiceDetailsResponse(
            id=result.id,
            invoice_number=result.invoice_number,
            patient_id=result.patient_id,
            issue_date=str(result.issue_date),
            due_date=str(result.due_date) if result.due_date else None,
            total_amount=float(result.total_amount),
            status=result.status,
            created_at=str(result.created_at),
            updated_at=str(result.updated_at)
        )
    finally:
        db.close()


@app.post("/invoices/{invoice_id}/payments", response_model=PaymentResponse, operation_id="record_payment")
async def record_payment(invoice_id: int, payload: PaymentRequest):
    db = SessionLocal()
    try:
        # Verify invoice exists and get patient_id
        inv_query = "SELECT id, patient_id FROM invoices WHERE id = :invoice_id"
        inv_result = db.execute(text(inv_query), {"invoice_id": invoice_id}).fetchone()
        
        if not inv_result:
            raise HTTPException(status_code=404, detail="Invoice not found")
        
        patient_id = inv_result.patient_id
        
        # Determine payment date
        p_date = parse_iso_date(payload.payment_date)
        
        # Get current user ID for audit trail
        creator_id = get_current_user_id()
        
        insert_query = """
            INSERT INTO payments (invoice_id, patient_id, amount, payment_date, payment_method, reference_number, notes, created_by)
            VALUES (:invoice_id, :patient_id, :amount, :payment_date, :payment_method, :reference_number, :notes, :created_by)
        """
        
        params = {
            "invoice_id": invoice_id,
            "patient_id": patient_id,
            "amount": payload.amount,
            "payment_date": p_date,
            "payment_method": payload.payment_method,
            "reference_number": payload.reference_number,
            "notes": payload.notes,
            "created_by": creator_id
        }
        
        db.execute(text(insert_query), params)
        db.commit()
        
        # Fetch the newly inserted record to return full details including generated IDs/timestamps
        select_query = """
            SELECT id, invoice_id, patient_id, amount, payment_date, payment_method, reference_number, notes, created_by, created_at, updated_at
            FROM payments
            WHERE id = last_insert_rowid()
        """
        new_record = db.execute(text(select_query)).fetchone()
        
        return PaymentResponse(
            id=new_record.id,
            invoice_id=new_record.invoice_id,
            patient_id=new_record.patient_id,
            amount=float(new_record.amount),
            payment_date=str(new_record.payment_date),
            payment_method=new_record.payment_method,
            reference_number=new_record.reference_number,
            notes=new_record.notes,
            created_by=new_record.created_by,
            created_at=str(new_record.created_at),
            updated_at=str(new_record.updated_at)
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/invoices/{invoice_id}/adjustments", response_model=AdjustmentResponse, operation_id="add_adjustment")
async def add_adjustment(invoice_id: int, payload: AdjustmentRequest):
    db = SessionLocal()
    try:
        # Verify invoice exists and get patient_id
        inv_query = "SELECT id, patient_id FROM invoices WHERE id = :invoice_id"
        inv_result = db.execute(text(inv_query), {"invoice_id": invoice_id}).fetchone()
        
        if not inv_result:
            raise HTTPException(status_code=404, detail="Invoice not found")
        
        patient_id = inv_result.patient_id
        
        # Determine adjustment date
        adj_date = parse_iso_date(payload.adjustment_date)
        
        # Get current user ID for audit trail
        creator_id = get_current_user_id()
        
        insert_query = """
            INSERT INTO adjustments (invoice_id, patient_id, amount, adjustment_type, reason, adjustment_date, created_by)
            VALUES (:invoice_id, :patient_id, :amount, :adjustment_type, :reason, :adjustment_date, :created_by)
        """
        
        params = {
            "invoice_id": invoice_id,
            "patient_id": patient_id,
            "amount": payload.amount,
            "adjustment_type": payload.adjustment_type,
            "reason": payload.reason,
            "adjustment_date": adj_date,
            "created_by": creator_id
        }
        
        db.execute(text(insert_query), params)
        db.commit()
        
        # Fetch the newly inserted record
        select_query = """
            SELECT id, invoice_id, patient_id, amount, adjustment_type, reason, adjustment_date, created_by, created_at, updated_at
            FROM adjustments
            WHERE id = last_insert_rowid()
        """
        new_record = db.execute(text(select_query)).fetchone()
        
        return AdjustmentResponse(
            id=new_record.id,
            invoice_id=new_record.invoice_id,
            patient_id=new_record.patient_id,
            amount=float(new_record.amount),
            adjustment_type=new_record.adjustment_type,
            reason=new_record.reason,
            adjustment_date=str(new_record.adjustment_date),
            created_by=new_record.created_by,
            created_at=str(new_record.created_at),
            updated_at=str(new_record.updated_at)
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
