import os
from typing import Optional, Literal
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class FinancingRequest(BaseModel):
    loan_amount: float = Field(..., description="Total loan amount requested")
    term_months: int = Field(..., description="Loan term in months")
    apr: float = Field(..., description="Annual Percentage Rate")
    monthly_payment: float = Field(..., description="Monthly payment amount")
    lender: Optional[str] = Field(None, description="Name of the lending institution")
    financing_type: Optional[Literal["loan", "lease", "cash"]] = Field(
        None, description="Type of financing used"
    )
    start_date: Optional[date] = Field(None, description="Start date of the financing")


class FinancingResponse(BaseModel):
    id: int
    deal_id: int
    loan_amount: float
    term_months: int
    apr: float
    monthly_payment: float
    lender: Optional[str]
    financing_type: Optional[str]
    start_date: Optional[date]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


@app.post(
    "/deals/{deal_id}/financing",
    response_model=FinancingResponse,
    operation_id="record_deal_financing",
    summary="Record financing details for a specific deal",
    tags=["PrimaryResource"],
)
def record_deal_financing(
    deal_id: int = Path(..., description="Unique identifier for the deal"),
    body: FinancingRequest = ...,
):
    db = SessionLocal()
    try:
        # Verify deal exists
        deal_result = db.execute(
            text("SELECT id FROM deals WHERE id = :deal_id"), {"deal_id": deal_id}
        ).fetchone()
        if not deal_result:
            raise HTTPException(status_code=404, detail="Deal not found")

        # Check if financing already exists for this deal (UNIQUE constraint on deal_id)
        existing_financing = db.execute(
            text("SELECT id FROM financing WHERE deal_id = :deal_id"),
            {"deal_id": deal_id},
        ).fetchone()
        if existing_financing:
            raise HTTPException(
                status_code=409, detail="Financing already recorded for this deal"
            )

        # Insert new financing record
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        
        insert_stmt = text("""
            INSERT INTO financing (
                deal_id, 
                loan_amount, 
                term_months, 
                apr, 
                monthly_payment, 
                lender, 
                financing_type, 
                start_date,
                created_at,
                updated_at
            ) VALUES (
                :deal_id, 
                :loan_amount, 
                :term_months, 
                :apr, 
                :monthly_payment, 
                :lender, 
                :financing_type, 
                :start_date,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
        """)

        params = {
            "deal_id": deal_id,
            "loan_amount": body.loan_amount,
            "term_months": body.term_months,
            "apr": body.apr,
            "monthly_payment": body.monthly_payment,
            "lender": body.lender,
            "financing_type": body.financing_type,
            "start_date": body.start_date.isoformat() if body.start_date else None,
        }

        result = db.execute(insert_stmt, params)
        db.commit()

        # Fetch the inserted record to return it
        new_id = result.lastrowid
        fetched = db.execute(
            text("""
                SELECT 
                    id, 
                    deal_id, 
                    loan_amount, 
                    term_months, 
                    apr, 
                    monthly_payment, 
                    lender, 
                    financing_type, 
                    start_date,
                    created_at,
                    updated_at
                FROM financing 
                WHERE id = :id
            """),
            {"id": new_id},
        ).fetchone()

        if not fetched:
            raise HTTPException(status_code=500, detail="Failed to retrieve inserted financing record")

        return FinancingResponse(
            id=fetched.id,
            deal_id=fetched.deal_id,
            loan_amount=fetched.loan_amount,
            term_months=fetched.term_months,
            apr=fetched.apr,
            monthly_payment=fetched.monthly_payment,
            lender=fetched.lender,
            financing_type=fetched.financing_type,
            start_date=date.fromisoformat(fetched.start_date) if fetched.start_date else None,
            created_at=datetime.fromisoformat(fetched.created_at) if fetched.created_at else None,
            updated_at=datetime.fromisoformat(fetched.updated_at) if fetched.updated_at else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
