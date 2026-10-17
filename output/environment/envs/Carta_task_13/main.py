import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


# --- Pydantic Models for Responses and Requests ---

class EquityGrantResponse(BaseModel):
    id: int
    grant_type: str
    shares: int
    grant_date: str  # Date string format YYYY-MM-DD
    status: str
    strike_price: Optional[float] = None
    vesting_start_date: Optional[str] = None
    vesting_schedule: Optional[str] = None


class UpdateEquityGrantStatusRequest(BaseModel):
    status: str = Field(..., description="New status for the equity grant", pattern="^accepted$")


class UpdateEquityGrantStatusResponse(BaseModel):
    id: int
    status: str
    updated_at: str  # ISO 8601 DateTime string


class RecordGrantDocumentRequest(BaseModel):
    grant_id: int
    document_type: str = Field(..., pattern="^(equity_agreement|tax_form)$")
    file_path: Optional[str] = None
    collected_by: int


class GrantDocumentResponse(BaseModel):
    id: int
    grant_id: int
    document_type: str
    collected_at: str  # ISO 8601 DateTime string
    collected_by: int


class EmployeeResponse(BaseModel):
    id: int
    user_id: int
    company_id: int
    employee_number: Optional[str] = None
    hire_date: Optional[str] = None


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    # Handle both 'T...Z' and space-separated formats
    try:
        # Try parsing with Z suffix first
        if dt_str.endswith('Z'):
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        else:
            return datetime.fromisoformat(dt_str)
    except ValueError:
        # Fallback for other common formats if needed, though spec implies standard ISO
        return None


# --- Endpoints ---

@app.get("/employees/{employee_id}/equity_grants", response_model=List[EquityGrantResponse], operation_id="find_employee_equity_grants")
def find_employee_equity_grants(
    employee_id: int = Path(..., description="Internal ID of the employee"),
    grant_type: Optional[str] = Query(None, description="Type of equity grant (e.g., ISO, NSO)"),
    status: Optional[str] = Query(None, description="Current status of the grant (e.g., pending, accepted)")
):
    """
    Retrieve all equity grants for a given employee, filtered by grant type and status if specified.
    """
    query = """
        SELECT 
            id, 
            grant_type, 
            shares, 
            grant_date, 
            status, 
            strike_price, 
            vesting_start_date, 
            vesting_schedule
        FROM equity_grants
        WHERE employee_id = :employee_id
    """
    params = {"employee_id": employee_id}

    if grant_type:
        query += " AND grant_type = :grant_type"
        params["grant_type"] = grant_type
    
    if status:
        query += " AND status = :status"
        params["status"] = status

    with engine.connect() as conn:
        result = conn.execute(text(query), params).fetchall()

    grants = []
    for row in result:
        grants.append(EquityGrantResponse(
            id=row.id,
            grant_type=row.grant_type,
            shares=row.shares,
            grant_date=str(row.grant_date),
            status=row.status,
            strike_price=float(row.strike_price) if row.strike_price is not None else None,
            vesting_start_date=str(row.vesting_start_date) if row.vesting_start_date is not None else None,
            vesting_schedule=row.vesting_schedule
        ))
    
    return grants


@app.patch("/equity_grants/{grant_id}", response_model=UpdateEquityGrantStatusResponse, operation_id="update_equity_grant_status")
def update_equity_grant_status(
    grant_id: int = Path(..., description="Internal ID of the equity grant"),
    body: UpdateEquityGrantStatusRequest = ...
):
    """
    Change the status of a specific equity grant to 'accepted'.
    """
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    # Verify grant exists
    check_query = "SELECT id FROM equity_grants WHERE id = :grant_id"
    with engine.connect() as conn:
        exists = conn.execute(text(check_query), {"grant_id": grant_id}).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Equity grant not found")

    # Update status
    update_query = """
        UPDATE equity_grants 
        SET status = :status, updated_at = :updated_at 
        WHERE id = :grant_id
    """
    params = {
        "status": body.status,
        "updated_at": now_utc,
        "grant_id": grant_id
    }
    
    with engine.begin() as conn:
        conn.execute(text(update_query), params)

    return UpdateEquityGrantStatusResponse(
        id=grant_id,
        status=body.status,
        updated_at=f"{now_utc}+00:00" # Approximate ISO format from DB string
    )


@app.post("/grant_documents", response_model=GrantDocumentResponse, operation_id="record_grant_document_collection")
def record_grant_document_collection(body: RecordGrantDocumentRequest):
    """
    Log that a specific document related to an equity grant has been collected.
    """
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    # Check if grant exists
    grant_check = "SELECT id FROM equity_grants WHERE id = :grant_id"
    with engine.connect() as conn:
        grant_exists = conn.execute(text(grant_check), {"grant_id": body.grant_id}).fetchone()
        if not grant_exists:
            raise HTTPException(status_code=404, detail="Equity grant not found")

    # Check if document already recorded for this grant/type (UNIQUE constraint on grant_id, document_type)
    doc_check = "SELECT id FROM grant_documents WHERE grant_id = :grant_id AND document_type = :document_type"
    with engine.connect() as conn:
        doc_exists = conn.execute(text(doc_check), {"grant_id": body.grant_id, "document_type": body.document_type}).fetchone()
        
        if doc_exists:
            # If it exists but wasn't collected before, we might need to update? 
            # The spec says "Record collection". The table has UNIQUE (grant_id, document_type).
            # Looking at initial snapshot, rows exist with null collected_at/by.
            # We should probably UPDATE the existing record rather than INSERT new one if unique constraint applies.
            
            update_doc_query = """
                UPDATE grant_documents 
                SET collected_at = :collected_at, 
                    collected_by = :collected_by, 
                    file_path = COALESCE(:file_path, file_path),
                    updated_at = :updated_at
                WHERE grant_id = :grant_id AND document_type = :document_type
            """
            params_update = {
                "collected_at": now_utc,
                "collected_by": body.collected_by,
                "file_path": body.file_path,
                "updated_at": now_utc,
                "grant_id": body.grant_id,
                "document_type": body.document_type
            }
            with engine.begin() as conn:
                conn.execute(text(update_doc_query), params_update)
                
            # Fetch the updated record to return correct ID
            fetch_query = "SELECT id, grant_id, document_type, collected_at, collected_by FROM grant_documents WHERE grant_id = :grant_id AND document_type = :document_type"
            with engine.connect() as conn:
                res = conn.execute(text(fetch_query), {"grant_id": body.grant_id, "document_type": body.document_type}).fetchone()
                
            return GrantDocumentResponse(
                id=res.id,
                grant_id=res.grant_id,
                document_type=res.document_type,
                collected_at=f"{res.collected_at}+00:00",
                collected_by=res.collected_by
            )
        else:
            # Insert new record
            insert_query = """
                INSERT INTO grant_documents (grant_id, document_type, file_path, collected_at, collected_by, created_at, updated_at)
                VALUES (:grant_id, :document_type, :file_path, :collected_at, :collected_by, :created_at, :updated_at)
            """
            params_insert = {
                "grant_id": body.grant_id,
                "document_type": body.document_type,
                "file_path": body.file_path,
                "collected_at": now_utc,
                "collected_by": body.collected_by,
                "created_at": now_utc,
                "updated_at": now_utc
            }
            with engine.begin() as conn:
                result = conn.execute(text(insert_query), params_insert)
                new_id = result.lastrowid
                
            return GrantDocumentResponse(
                id=new_id,
                grant_id=body.grant_id,
                document_type=body.document_type,
                collected_at=f"{now_utc}+00:00",
                collected_by=body.collected_by
            )


@app.get("/users/{user_id}/companies/{company_id}/employees", response_model=EmployeeResponse, operation_id="find_employee_by_user_and_company")
def find_employee_by_user_and_company(
    user_id: int = Path(..., description="Internal ID of the user"),
    company_id: int = Path(..., description="Internal ID of the company")
):
    """
    Locate an employee record using their user ID and company ID.
    """
    query = """
        SELECT id, user_id, company_id, employee_number, hire_date
        FROM employees
        WHERE user_id = :user_id AND company_id = :company_id
    """
    params = {"user_id": user_id, "company_id": company_id}
    
    with engine.connect() as conn:
        row = conn.execute(text(query), params).fetchone()
        
    if not row:
        raise HTTPException(status_code=404, detail="Employee not found")
        
    return EmployeeResponse(
        id=row.id,
        user_id=row.user_id,
        company_id=row.company_id,
        employee_number=row.employee_number,
        hire_date=str(row.hire_date) if row.hire_date else None
    )
