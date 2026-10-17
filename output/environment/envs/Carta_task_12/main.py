import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_URL = os.environ.get("DATABASE_PATH")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class StakeholderResponse(BaseModel):
    id: int
    name: str
    stakeholder_type: str


class AddSharesRequest(BaseModel):
    shares: int = Field(..., gt=0)
    price_per_share: float = Field(..., ge=0.0)
    activity_date: str  # YYYY-MM-DD format


class AddSharesResponse(BaseModel):
    holding_id: int
    new_total_shares: int
    new_total_cost_basis: float
    capital_activity_id: int


@app.get("/funds/{fund_id}/stakeholders", response_model=List[StakeholderResponse], operation_id="find_fund_stakeholders")
def find_fund_stakeholders(fund_id: int):
    """
    Retrieve stakeholder records associated with a fund.
    """
    db = SessionLocal()
    try:
        query = text("""
            SELECT id, name, stakeholder_type 
            FROM stakeholders 
            WHERE fund_id = :fund_id
        """)
        result = db.execute(query, {"fund_id": fund_id})
        rows = result.fetchall()
        
        stakeholders = []
        for row in rows:
            stakeholders.append({
                "id": row.id,
                "name": row.name,
                "stakeholder_type": row.stakeholder_type
            })
            
        return stakeholders
    finally:
        db.close()


@app.post("/funds/{fund_id}/companies/{company_id}/holdings", response_model=AddSharesResponse, operation_id="add_series_a_shares")
def add_series_a_shares(
    fund_id: int,
    company_id: int,
    payload: AddSharesRequest,
    stakeholder_id: int = Query(..., description="Internal ID of the stakeholder record associated with the fund")
):
    """
    Record additional Series A shares acquired for a fund's portfolio holding.
    Updates total shares and cost basis while preserving prior capital activities.
    """
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User context missing")
    
    try:
        created_by = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user context")

    db = SessionLocal()
    try:
        # Verify fund exists
        fund_check = db.execute(text("SELECT id FROM funds WHERE id = :fid"), {"fid": fund_id}).fetchone()
        if not fund_check:
            raise HTTPException(status_code=404, detail="Fund not found")

        # Verify company exists
        company_check = db.execute(text("SELECT id FROM companies WHERE id = :cid"), {"cid": company_id}).fetchone()
        if not company_check:
            raise HTTPException(status_code=404, detail="Company not found")

        # Verify stakeholder belongs to the fund
        stakeholder_check = db.execute(
            text("SELECT id FROM stakeholders WHERE id = :sid AND fund_id = :fid"),
            {"sid": stakeholder_id, "fid": fund_id}
        ).fetchone()
        if not stakeholder_check:
            raise HTTPException(status_code=404, detail="Stakeholder not found or does not belong to this fund")

        # Find existing holding
        holding_row = db.execute(
            text("""
                SELECT id, total_shares, total_cost_basis 
                FROM portfolio_holdings 
                WHERE fund_id = :fid AND company_id = :cid
            """),
            {"fid": fund_id, "cid": company_id}
        ).fetchone()

        if holding_row:
            holding_id = holding_row.id
            old_total_shares = holding_row.total_shares
            old_total_cost_basis = holding_row.total_cost_basis
            
            new_total_shares = old_total_shares + payload.shares
            added_cost = payload.shares * payload.price_per_share
            new_total_cost_basis = old_total_cost_basis + added_cost

            # Update holding
            db.execute(
                text("""
                    UPDATE portfolio_holdings 
                    SET total_shares = :new_shares, 
                        total_cost_basis = :new_cost,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :hid
                """),
                {
                    "new_shares": new_total_shares,
                    "new_cost": new_total_cost_basis,
                    "hid": holding_id
                }
            )
        else:
            # Create new holding if it doesn't exist (though spec implies updating existing, robust handling is good)
            new_total_shares = payload.shares
            new_total_cost_basis = payload.shares * payload.price_per_share
            
            insert_res = db.execute(
                text("""
                    INSERT INTO portfolio_holdings (fund_id, company_id, stakeholder_id, total_shares, total_cost_basis, created_at, updated_at)
                    VALUES (:fid, :cid, :sid, :ts, :tcb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {
                    "fid": fund_id,
                    "cid": company_id,
                    "sid": stakeholder_id,
                    "ts": new_total_shares,
                    "tcb": new_total_cost_basis
                }
            )
            holding_id = insert_res.lastrowid

        # Insert capital activity
        cap_act_res = db.execute(
            text("""
                INSERT INTO capital_activities (holding_id, activity_type, shares, price_per_share, activity_date, notes, created_by, created_at, updated_at)
                VALUES (:hid, 'acquisition', :shares, :pps, :date, :notes, :created_by, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "hid": holding_id,
                "shares": payload.shares,
                "pps": payload.price_per_share,
                "date": payload.activity_date,
                "notes": f"Series A financing acquisition ({payload.shares} shares @ ${payload.price_per_share})",
                "created_by": created_by
            }
        )
        capital_activity_id = cap_act_res.lastrowid

        db.commit()

        return AddSharesResponse(
            holding_id=holding_id,
            new_total_shares=new_total_shares,
            new_total_cost_basis=new_total_cost_basis,
            capital_activity_id=capital_activity_id
        )

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
