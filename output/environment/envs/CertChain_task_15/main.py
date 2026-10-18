import os
from datetime import date, datetime
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    create_engine,
    text,
    Column,
    Integer,
    String,
    Date,
    Float,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.orm import declarative_base, Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
app = FastAPI(title="CertChain Renewal Eligibility API")

engine = create_engine(DATABASE_PATH)
Base = declarative_base()


# --- Pydantic Models ---

class RenewalEligibilityResponse(BaseModel):
    certificant_id: int
    user_id: int
    full_name: str
    email: str
    certification_date: str
    expiration_date: str
    total_credits_earned: float
    required_credits: float
    is_eligible: bool
    status: str


# --- SQLAlchemy Models (for reference/mapping if needed, but we use raw SQL mostly) ---
# Note: We don't need to define full ORM models if we use raw SQL with fetchall and map manually,
# but defining them helps with type hinting if we were using ORM. 
# Given the constraint to use parameterized SQL and existing tables, raw SQL is safer for exact control.

def get_db_session():
    """Yield a database session."""
    with Session(engine) as session:
        yield session


@app.get(
    "/certificants/renewal-eligibility",
    operation_id="calculate_renewal_eligibility",
    summary="Calculate renewal eligibility for certificants in the 'Certified Data Analyst' program",
    description="Calculate renewal eligibility for certificants in the 'Certified Data Analyst' program for the current renewal cycle ending 2025-12-31. Returns a report of those who are eligible vs. not eligible based on meeting the required continuing education credits.",
    response_model=List[RenewalEligibilityResponse],
    tags=["PrimaryResource"],
)
def calculate_renewal_eligibility(
    program_name: str = Query(..., description="Name of the program to check eligibility for"),
    renewal_cycle_end_date: str = Query(..., description="End date of the renewal cycle (format: YYYY-MM-DD)")
):
    """
    Calculate renewal eligibility for certificants.
    
    Args:
        program_name: Name of the program (e.g., 'Certified Data Analyst')
        renewal_cycle_end_date: End date of the renewal cycle (YYYY-MM-DD)
        
    Returns:
        List of certificants with their eligibility status
    """
    # Parse the renewal cycle end date
    try:
        cycle_end_date = datetime.strptime(renewal_cycle_end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    query = text("""
        SELECT 
            c.id AS certificant_id,
            c.user_id,
            u.full_name,
            u.email,
            c.certification_date,
            c.expiration_date,
            COALESCE(SUM(cr.credit_amount), 0) AS total_credits_earned,
            rr.required_credits,
            CASE 
                WHEN COALESCE(SUM(cr.credit_amount), 0) >= rr.required_credits THEN 1 
                ELSE 0 
            END AS is_eligible,
            c.status
        FROM 
            certificants c
        JOIN 
            users u ON c.user_id = u.id
        JOIN 
            programs p ON c.program_id = p.id
        JOIN 
            renewal_requirements rr ON c.program_id = rr.program_id
        LEFT JOIN 
            credits cr ON c.id = cr.certificant_id
        WHERE 
            p.name = :program_name
            AND c.expiration_date = :cycle_end_date
            AND c.status IN ('active', 'expired') -- Consider active or expired for renewal
        GROUP BY 
            c.id, c.user_id, u.full_name, u.email, c.certification_date, c.expiration_date, rr.required_credits, c.status
        ORDER BY 
            c.id ASC
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {
            "program_name": program_name,
            "cycle_end_date": cycle_end_date.isoformat()
        })
        rows = result.fetchall()

    responses = []
    for row in rows:
        responses.append(RenewalEligibilityResponse(
            certificant_id=row[0],
            user_id=row[1],
            full_name=row[2],
            email=row[3],
            certification_date=row[4],
            expiration_date=row[5],
            total_credits_earned=float(row[6]),
            required_credits=float(row[7]),
            is_eligible=bool(row[8]),
            status=row[9]
        ))

    return responses
