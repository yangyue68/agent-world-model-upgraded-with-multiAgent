import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="AutoBridge Dealer API")

# Pydantic Models for Request/Response Bodies

class FinancingUpdateRequest(BaseModel):
    term_months: int = Field(..., description="New loan term in months")
    apr: float = Field(..., description="New annual percentage rate")

class FinancingUpdateResponse(BaseModel):
    deal_id: int
    term_months: int
    apr: float

class AddOnCreateRequest(BaseModel):
    add_on_id: int = Field(..., description="Internal ID of the add-on to add")
    price: float = Field(..., description="Price of the add-on")

class AddOnCreateResponse(BaseModel):
    deal_id: int
    add_on_id: int
    price: float


@app.put("/deals/{deal_id}/financing", response_model=FinancingUpdateResponse, operation_id="update_deal_financing")
def update_deal_financing(deal_id: int = Path(..., description="Internal ID of the deal to modify"), body: FinancingUpdateRequest = None):
    """
    Update financing terms for a deal.
    """
    db = SessionLocal()
    try:
        # Check if deal exists
        deal_result = db.execute(text("SELECT id FROM deals WHERE id = :id"), {"id": deal_id}).fetchone()
        if not deal_result:
            raise HTTPException(status_code=404, detail="Deal not found")

        # Check if financing terms exist for this deal
        financing_result = db.execute(text("SELECT id FROM financing_terms WHERE deal_id = :deal_id"), {"deal_id": deal_id}).fetchone()
        
        if financing_result:
            # Update existing financing terms
            db.execute(
                text("""
                    UPDATE financing_terms 
                    SET term_months = :term_months, apr = :apr, updated_at = CURRENT_TIMESTAMP
                    WHERE deal_id = :deal_id
                """),
                {
                    "term_months": body.term_months,
                    "apr": body.apr,
                    "deal_id": deal_id
                }
            )
        else:
            # Insert new financing terms
            db.execute(
                text("""
                    INSERT INTO financing_terms (deal_id, term_months, apr, created_at, updated_at)
                    VALUES (:deal_id, :term_months, :apr, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {
                    "deal_id": deal_id,
                    "term_months": body.term_months,
                    "apr": body.apr
                }
            )
        
        db.commit()
        
        return FinancingUpdateResponse(
            deal_id=deal_id,
            term_months=body.term_months,
            apr=body.apr
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/deals/{deal_id}/add-ons", response_model=AddOnCreateResponse, operation_id="add_deal_add_on")
def add_deal_add_on(deal_id: int = Path(..., description="Internal ID of the deal to modify"), body: AddOnCreateRequest = None):
    """
    Add an add-on to a deal.
    """
    db = SessionLocal()
    try:
        # Check if deal exists
        deal_result = db.execute(text("SELECT id FROM deals WHERE id = :id"), {"id": deal_id}).fetchone()
        if not deal_result:
            raise HTTPException(status_code=404, detail="Deal not found")

        # Check if add-on exists
        addon_result = db.execute(text("SELECT id FROM add_ons WHERE id = :id"), {"id": body.add_on_id}).fetchone()
        if not addon_result:
            raise HTTPException(status_code=404, detail="Add-on not found")

        # Check if this add-on is already attached to the deal
        existing_addon = db.execute(
            text("SELECT id FROM deal_add_ons WHERE deal_id = :deal_id AND add_on_id = :add_on_id"),
            {"deal_id": deal_id, "add_on_id": body.add_on_id}
        ).fetchone()

        if existing_addon:
            # Update existing entry
            db.execute(
                text("""
                    UPDATE deal_add_ons 
                    SET price = :price, updated_at = CURRENT_TIMESTAMP
                    WHERE deal_id = :deal_id AND add_on_id = :add_on_id
                """),
                {
                    "price": body.price,
                    "deal_id": deal_id,
                    "add_on_id": body.add_on_id
                }
            )
        else:
            # Insert new entry
            db.execute(
                text("""
                    INSERT INTO deal_add_ons (deal_id, add_on_id, price, quantity, created_at, updated_at)
                    VALUES (:deal_id, :add_on_id, :price, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {
                    "deal_id": deal_id,
                    "add_on_id": body.add_on_id,
                    "price": body.price
                }
            )
        
        db.commit()
        
        return AddOnCreateResponse(
            deal_id=deal_id,
            add_on_id=body.add_on_id,
            price=body.price
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
