import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

app = FastAPI()


class InvestorSearchResult(BaseModel):
    id: int
    full_name: str
    email: str


class HoldingDetail(BaseModel):
    company_name: str
    security_type: str
    quantity: Optional[float]
    principal_amount: Optional[float]
    status: str
    latest_valuation_date: Optional[str]
    estimated_holding_value: Optional[float]


@app.get("/investors/search", response_model=List[InvestorSearchResult], operation_id="search_investors")
def search_investors(
    name: Optional[str] = Query(None, description="Partial or full name of the investor"),
    email: Optional[str] = Query(None, description="Email address of the investor"),
):
    """
    Find investors by their full name or email address. Returns matching investor records with IDs for further queries.
    """
    query_parts = []
    params = {}

    if name:
        query_parts.append("u.full_name LIKE :name_pattern")
        params["name_pattern"] = f"%{name}%"
    
    if email:
        query_parts.append("u.email LIKE :email_pattern")
        params["email_pattern"] = f"%{email}%"

    where_clause = ""
    if query_parts:
        where_clause = "WHERE " + " AND ".join(query_parts)

    sql = f"""
        SELECT i.id, u.full_name, u.email
        FROM investors i
        JOIN users u ON i.user_id = u.id
        {where_clause}
    """

    try:
        with SessionLocal() as db:
            result = db.execute(text(sql), params).fetchall()
            return [
                InvestorSearchResult(id=row.id, full_name=row.full_name, email=row.email)
                for row in result
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/investors/{investor_id}/holdings", response_model=List[HoldingDetail], operation_id="find_investor_holdings")
def find_investor_holdings(investor_id: int):
    """
    Retrieve all active securities held by a given investor, including company details, security type, 
    quantity/principal amount, status, latest valuation date, and estimated holding value where available.
    """
    # We need to join holdings -> securities -> companies/security_types
    # And left join valuations to get the LATEST valuation per security.
    # To get the latest valuation efficiently in SQLite without window functions (though modern sqlite supports them),
    # we can use a subquery or MAX(valuation_date) grouped by security_id.
    
    sql = """
        WITH LatestValuations AS (
            SELECT 
                v.security_id,
                MAX(v.valuation_date) as max_val_date
            FROM valuations v
            GROUP BY v.security_id
        )
        SELECT 
            c.display_name as company_name,
            st.name as security_type,
            h.quantity,
            h.principal_amount,
            h.status,
            lvm.max_val_date as latest_valuation_date,
            CASE 
                WHEN lvm.max_val_date IS NOT NULL THEN
                    COALESCE(h.quantity * v.price_per_share, h.principal_amount)
                ELSE NULL
            END as estimated_holding_value
        FROM holdings h
        JOIN securities s ON h.security_id = s.id
        JOIN companies c ON s.company_id = c.id
        JOIN security_types st ON s.security_type_id = st.id
        LEFT JOIN LatestValuations lvm ON s.id = lvm.security_id
        LEFT JOIN valuations v ON s.id = v.security_id AND lvm.max_val_date = v.valuation_date
        WHERE h.investor_id = :investor_id
          AND h.status = 'active'
    """

    try:
        with SessionLocal() as db:
            result = db.execute(text(sql), {"investor_id": investor_id}).fetchall()
            
            output = []
            for row in result:
                # Format date string if present
                val_date_str = None
                if row.latest_valuation_date:
                    # SQLAlchemy might return datetime.date object depending on driver/type coercion
                    # The schema says DATE, so it should be safe to convert to isoformat
                    if hasattr(row.latest_valuation_date, 'isoformat'):
                        val_date_str = row.latest_valuation_date.isoformat()
                    else:
                        val_date_str = str(row.latest_valuation_date)

                est_val = row.estimated_holding_value
                
                output.append(HoldingDetail(
                    company_name=row.company_name,
                    security_type=row.security_type,
                    quantity=row.quantity,
                    principal_amount=row.principal_amount,
                    status=row.status,
                    latest_valuation_date=val_date_str,
                    estimated_holding_value=float(est_val) if est_val is not None else None
                ))
            
            return output
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
