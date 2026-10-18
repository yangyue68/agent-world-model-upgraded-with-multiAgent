import os
from datetime import datetime, date
from typing import Optional, List
from typing import Literal

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Pydantic Models for Request/Response

class CreateRSUGrantRequest(BaseModel):
    company_id: int
    employee_id: int
    shares_total: int
    vesting_start_date: str  # ISO-8601 date string
    vesting_frequency: Literal["monthly", "quarterly", "annually"]
    vesting_period_years: int

class GrantResponse(BaseModel):
    id: int
    status: str

class ApprovalItem(BaseModel):
    id: int
    approver_role: str
    status: str

class ApproveGrantRequest(BaseModel):
    approver_role: Literal["CFO", "legal_advisor"]

class UploadDocumentRequest(BaseModel):
    document_type: Literal["signed_grant_agreement", "tax_documentation", "board_consent", "other"]
    file_name: str

class DocumentResponse(BaseModel):
    id: int
    document_type: str


def get_current_user_id():
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")


@app.post("/grants", response_model=GrantResponse, operation_id="create_rsu_grant")
def create_rsu_grant(request: CreateRSUGrantRequest):
    """
    Creates a new RSU grant.
    Validates that the employee belongs to the company.
    Sets initial status to 'draft'.
    Generates vesting schedule based on quarterly/monthly/annually frequency.
    """
    db = SessionLocal()
    try:
        # Validate Employee and Company relationship
        emp_query = text("""
            SELECT e.id, e.company_id 
            FROM employees e 
            WHERE e.id = :employee_id AND e.company_id = :company_id
        """)
        emp_result = db.execute(emp_query, {"employee_id": request.employee_id, "company_id": request.company_id}).fetchone()
        
        if not emp_result:
            raise HTTPException(status_code=404, detail="Employee not found in specified company")

        # Parse dates
        try:
            start_date = datetime.fromisoformat(request.vesting_start_date.replace('Z', '+00:00')).date()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid vesting_start_date format")

        # Determine initial status
        # Spec says "Requires the grant to be approved by CFO and legal advisor before activation."
        # Usually creation starts as draft or pending_approval. Given the workflow (Create -> Approve -> Upload Docs -> Activate),
        # we can start as 'draft' or 'pending_approval'. Let's use 'draft' as per schema default, but logically it needs approvals.
        # The activate endpoint checks conditions.
        initial_status = "draft"

        insert_grant = text("""
            INSERT INTO grants (
                company_id, employee_id, grant_type, shares_total, 
                vesting_start_date, vesting_frequency, vesting_period_years, 
                status, created_by
            ) VALUES (
                :company_id, :employee_id, 'RSU', :shares_total, 
                :vesting_start_date, :vesting_frequency, :vesting_period_years, 
                :status, :created_by
            )
        """)
        
        current_user_id = get_current_user_id()
        
        result = db.execute(insert_grant, {
            "company_id": request.company_id,
            "employee_id": request.employee_id,
            "shares_total": request.shares_total,
            "vesting_start_date": start_date,
            "vesting_frequency": request.vesting_frequency,
            "vesting_period_years": request.vesting_period_years,
            "status": initial_status,
            "created_by": current_user_id
        })
        
        grant_id = result.lastrowid
        
        # Generate Vesting Schedule
        # Quarterly over 4 years = 16 periods. Monthly = 48. Annually = 4.
        total_shares = request.shares_total
        freq = request.vesting_frequency.lower()
        years = request.vesting_period_years
        
        if freq == "quarterly":
            periods_per_year = 4
        elif freq == "monthly":
            periods_per_year = 12
        elif freq == "annually":
            periods_per_year = 1
        else:
            raise HTTPException(status_code=400, detail="Invalid vesting frequency")
            
        total_periods = periods_per_year * years
        shares_per_period = total_shares // total_periods
        remainder = total_shares % total_periods
        
        cumulative = 0
        current_date = start_date
        
        # Helper to add months/years
        def add_months(d, m):
            month = d.month - 1 + m
            year = d.year + month // 12
            month = month % 12 + 1
            day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
            return date(year, month, day)

        def add_years(d, y):
            try:
                return date(d.year + y, d.month, d.day)
            except ValueError:
                # Handle Feb 29 leap year edge case
                return date(d.year + y, d.month, 28)

        insert_schedule = text("""
            INSERT INTO vesting_schedules (grant_id, vesting_date, shares_vested, cumulative_shares)
            VALUES (:grant_id, :vesting_date, :shares_vested, :cumulative_shares)
        """)

        for i in range(total_periods):
            if freq == "quarterly":
                next_date = add_months(current_date, 3)
            elif freq == "monthly":
                next_date = add_months(current_date, 1)
            elif freq == "annually":
                next_date = add_years(current_date, 1)
            
            # Calculate shares for this period
            # Distribute remainder to last few periods or first? Standard is often equal split with remainder at end or beginning.
            # Let's put remainder in the last period for simplicity, or distribute evenly.
            # Simple approach: integer division, add remainder to last period.
            if i < total_periods - 1:
                vested = shares_per_period
            else:
                vested = shares_per_period + remainder
            
            cumulative += vested
            
            db.execute(insert_schedule, {
                "grant_id": grant_id,
                "vesting_date": next_date,
                "shares_vested": vested,
                "cumulative_shares": cumulative
            })
            
            current_date = next_date

        db.commit()
        
        return GrantResponse(id=grant_id, status=initial_status)
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/grants/{grant_id}/approvals", response_model=List[ApprovalItem], operation_id="get_grant_approvals")
def get_grant_approvals(grant_id: int = Path(...)):
    db = SessionLocal()
    try:
        query = text("""
            SELECT id, approver_role, status 
            FROM grant_approvals 
            WHERE grant_id = :grant_id
        """)
        results = db.execute(query, {"grant_id": grant_id}).fetchall()
        
        return [
            ApprovalItem(id=r.id, approver_role=r.approver_role, status=r.status)
            for r in results
        ]
    finally:
        db.close()


@app.post("/grants/{grant_id}/approve", response_model=GrantResponse, operation_id="approve_grant")
def approve_grant(request: ApproveGrantRequest, grant_id: int = Path(...)):
    """
    Records approval for a specific role.
    If both CFO and Legal Advisor are approved, updates grant status to 'approved'.
    """
    db = SessionLocal()
    try:
        # Check if grant exists
        grant = db.execute(text("SELECT id, status FROM grants WHERE id = :id"), {"id": grant_id}).fetchone()
        if not grant:
            raise HTTPException(status_code=404, detail="Grant not found")
        
        # Check if approval record exists for this role, if not create it
        existing_approval = db.execute(
            text("SELECT id, status FROM grant_approvals WHERE grant_id = :gid AND approver_role = :role"),
            {"gid": grant_id, "role": request.approver_role}
        ).fetchone()
        
        current_user_id = get_current_user_id()
        now = datetime.utcnow()
        
        if existing_approval:
            if existing_approval.status == 'approved':
                # Already approved, maybe just return success or update timestamp?
                pass
            else:
                db.execute(
                    text("UPDATE grant_approvals SET status = 'approved', approved_at = :now, approver_user_id = :uid, updated_at = :now WHERE id = :id"),
                    {"now": now, "uid": current_user_id, "id": existing_approval.id}
                )
        else:
            db.execute(
                text("""
                    INSERT INTO grant_approvals (grant_id, approver_role, approver_user_id, status, approved_at, created_at, updated_at)
                    VALUES (:gid, :role, :uid, 'approved', :now, :now, :now)
                """),
                {"gid": grant_id, "role": request.approver_role, "uid": current_user_id, "now": now}
            )
        
        # Check if all required roles (CFO, legal_advisor) are approved
        # Note: Schema allows other roles like CEO/board, but spec says "Require approval from the company's CFO and legal advisor".
        # We assume these two are mandatory for 'approved' status transition.
        
        cfo_approved = db.execute(
            text("SELECT count(*) FROM grant_approvals WHERE grant_id = :gid AND approver_role = 'CFO' AND status = 'approved'"),
            {"gid": grant_id}
        ).scalar() > 0
        
        legal_approved = db.execute(
            text("SELECT count(*) FROM grant_approvals WHERE grant_id = :gid AND approver_role = 'legal_advisor' AND status = 'approved'"),
            {"gid": grant_id}
        ).scalar() > 0
        
        new_status = grant.status
        if cfo_approved and legal_approved:
            # Only change to 'approved' if currently 'draft' or 'pending_approval'
            if grant.status in ['draft', 'pending_approval']:
                new_status = 'approved'
                db.execute(
                    text("UPDATE grants SET status = :status, updated_at = :now WHERE id = :id"),
                    {"status": new_status, "now": now, "id": grant_id}
                )
                
        db.commit()
        
        return GrantResponse(id=grant_id, status=new_status)
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/grants/{grant_id}/documents", response_model=DocumentResponse, operation_id="upload_grant_document")
def upload_grant_document(request: UploadDocumentRequest, grant_id: int = Path(...)):
    """
    Records receipt of a document.
    """
    db = SessionLocal()
    try:
        # Verify grant exists
        grant = db.execute(text("SELECT id FROM grants WHERE id = :id"), {"id": grant_id}).fetchone()
        if not grant:
            raise HTTPException(status_code=404, detail="Grant not found")
        
        current_user_id = get_current_user_id()
        now = datetime.utcnow()
        
        insert_doc = text("""
            INSERT INTO grant_documents (grant_id, document_type, file_name, received_at, received_by, created_at, updated_at)
            VALUES (:gid, :dtype, :fname, :received_at, :received_by, :created_at, :updated_at)
        """)
        
        result = db.execute(insert_doc, {
            "gid": grant_id,
            "dtype": request.document_type,
            "fname": request.file_name,
            "received_at": now,
            "received_by": current_user_id,
            "created_at": now,
            "updated_at": now
        })
        
        doc_id = result.lastrowid
        db.commit()
        
        return DocumentResponse(id=doc_id, document_type=request.document_type)
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.put("/grants/{grant_id}/activate", response_model=GrantResponse, operation_id="activate_grant")
def activate_grant(grant_id: int = Path(...)):
    """
    Activates a grant if:
    1. Status is 'approved' (implies CFO and Legal approved).
    2. Signed grant agreement is uploaded.
    3. Tax documentation is uploaded.
    """
    db = SessionLocal()
    try:
        grant = db.execute(text("SELECT id, status FROM grants WHERE id = :id"), {"id": grant_id}).fetchone()
        if not grant:
            raise HTTPException(status_code=404, detail="Grant not found")
        
        if grant.status != 'approved':
            raise HTTPException(status_code=400, detail=f"Grant must be in 'approved' status to activate. Current status: {grant.status}")
        
        # Check documents
        has_agreement = db.execute(
            text("SELECT count(*) FROM grant_documents WHERE grant_id = :gid AND document_type = 'signed_grant_agreement'"),
            {"gid": grant_id}
        ).scalar() > 0
        
        has_tax_docs = db.execute(
            text("SELECT count(*) FROM grant_documents WHERE grant_id = :gid AND document_type = 'tax_documentation'"),
            {"gid": grant_id}
        ).scalar() > 0
        
        if not has_agreement:
            raise HTTPException(status_code=400, detail="Missing signed_grant_agreement document")
        if not has_tax_docs:
            raise HTTPException(status_code=400, detail="Missing tax_documentation document")
        
        now = datetime.utcnow()
        db.execute(
            text("UPDATE grants SET status = 'active', updated_at = :now WHERE id = :id"),
            {"now": now, "id": grant_id}
        )
        
        db.commit()
        
        return GrantResponse(id=grant_id, status='active')
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
