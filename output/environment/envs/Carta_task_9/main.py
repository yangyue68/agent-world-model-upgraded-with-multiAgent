import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CreateRoundRequest(BaseModel):
    company_id: int
    name: str
    pre_money_valuation: float
    target_raise: float
    closing_date: str


class RoundResponse(BaseModel):
    id: int
    company_id: int
    name: str
    pre_money_valuation: float
    target_raise: float
    status: str
    closing_date: str
    created_by: int


class CreateShareClassRequest(BaseModel):
    financing_round_id: int
    name: str
    issue_price: float
    liquidation_preference_multiple: float
    liquidation_preference_type: str


class ShareClassResponse(BaseModel):
    id: int
    financing_round_id: int
    name: str
    issue_price: float
    liquidation_preference_multiple: float
    liquidation_preference_type: str


class InviteInvestorRequest(BaseModel):
    financing_round_id: int
    investor_id: int
    amount: float


class InvestorCommitmentResponse(BaseModel):
    id: int
    financing_round_id: int
    investor_id: int
    amount: float
    status: str


@app.post("/financing_rounds", response_model=RoundResponse, operation_id="create_series_a_round")
def create_series_a_round(payload: CreateRoundRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    # Verify company exists
    with engine.connect() as conn:
        res = conn.execute(text("SELECT id FROM companies WHERE id = :cid"), {"cid": payload.company_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Company not found")

        # Insert new round
        insert_stmt = text("""
            INSERT INTO financing_rounds (company_id, name, pre_money_valuation, target_raise, status, closing_date, created_by)
            VALUES (:company_id, :name, :pre_money_valuation, :target_raise, 'pending_approval', :closing_date, :created_by)
        """)
        result = conn.execute(insert_stmt, {
            "company_id": payload.company_id,
            "name": payload.name,
            "pre_money_valuation": payload.pre_money_valuation,
            "target_raise": payload.target_raise,
            "closing_date": payload.closing_date,
            "created_by": current_user_id
        })
        conn.commit()
        
        new_id = result.lastrowid
        
        # Fetch the inserted row to return correct data types and defaults
        fetch_stmt = text("""
            SELECT id, company_id, name, pre_money_valuation, target_raise, status, closing_date, created_by
            FROM financing_rounds
            WHERE id = :rid
        """)
        row = conn.execute(fetch_stmt, {"rid": new_id}).fetchone()
        
        return RoundResponse(
            id=row.id,
            company_id=row.company_id,
            name=row.name,
            pre_money_valuation=row.pre_money_valuation,
            target_raise=row.target_raise,
            status=row.status,
            closing_date=str(row.closing_date),
            created_by=row.created_by
        )


@app.post("/share_classes", response_model=ShareClassResponse, operation_id="create_preferred_stock_class")
def create_preferred_stock_class(payload: CreateShareClassRequest):
    # Validate enum values against schema constraints
    valid_liq_types = ['non_participating', 'participating']
    if payload.liquidation_preference_type not in valid_liq_types:
        raise HTTPException(status_code=422, detail=f"Invalid liquidation_preference_type. Must be one of {valid_liq_types}")

    with engine.connect() as conn:
        # Verify financing round exists
        res = conn.execute(text("SELECT id FROM financing_rounds WHERE id = :rid"), {"rid": payload.financing_round_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Financing round not found")

        insert_stmt = text("""
            INSERT INTO share_classes (financing_round_id, name, issue_price, liquidation_preference_multiple, liquidation_preference_type)
            VALUES (:fid, :name, :ip, :lpm, :lpt)
        """)
        result = conn.execute(insert_stmt, {
            "fid": payload.financing_round_id,
            "name": payload.name,
            "ip": payload.issue_price,
            "lpm": payload.liquidation_preference_multiple,
            "lpt": payload.liquidation_preference_type
        })
        conn.commit()
        
        new_id = result.lastrowid
        
        fetch_stmt = text("""
            SELECT id, financing_round_id, name, issue_price, liquidation_preference_multiple, liquidation_preference_type
            FROM share_classes
            WHERE id = :sid
        """)
        row = conn.execute(fetch_stmt, {"sid": new_id}).fetchone()
        
        return ShareClassResponse(
            id=row.id,
            financing_round_id=row.financing_round_id,
            name=row.name,
            issue_price=row.issue_price,
            liquidation_preference_multiple=row.liquidation_preference_multiple,
            liquidation_preference_type=row.liquidation_preference_type
        )


@app.post("/investor_commitments", response_model=InvestorCommitmentResponse, operation_id="invite_investor_commitment")
def invite_investor_commitment(payload: InviteInvestorRequest):
    with engine.connect() as conn:
        # Verify financing round exists
        res_round = conn.execute(text("SELECT id FROM financing_rounds WHERE id = :rid"), {"rid": payload.financing_round_id})
        if not res_round.fetchone():
            raise HTTPException(status_code=404, detail="Financing round not found")

        # Verify investor exists
        res_inv = conn.execute(text("SELECT id FROM investors WHERE id = :iid"), {"iid": payload.investor_id})
        if not res_inv.fetchone():
            raise HTTPException(status_code=404, detail="Investor not found")

        insert_stmt = text("""
            INSERT INTO investor_commitments (financing_round_id, investor_id, amount, status)
            VALUES (:fid, :iid, :amt, 'pending')
        """)
        result = conn.execute(insert_stmt, {
            "fid": payload.financing_round_id,
            "iid": payload.investor_id,
            "amt": payload.amount
        })
        conn.commit()
        
        new_id = result.lastrowid
        
        fetch_stmt = text("""
            SELECT id, financing_round_id, investor_id, amount, status
            FROM investor_commitments
            WHERE id = :cid
        """)
        row = conn.execute(fetch_stmt, {"cid": new_id}).fetchone()
        
        return InvestorCommitmentResponse(
            id=row.id,
            financing_round_id=row.financing_round_id,
            investor_id=row.investor_id,
            amount=row.amount,
            status=row.status
        )
