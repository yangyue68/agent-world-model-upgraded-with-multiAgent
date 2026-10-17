import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class GiftResponse(BaseModel):
    id: int
    constituent_id: int
    campaign_id: Optional[int]
    fund_id: Optional[int]
    amount: float
    gift_date: str
    notes: Optional[str]


class ReassignGiftRequest(BaseModel):
    campaign_name: str
    fund_name: str


def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()


@app.get(
    "/campaigns/{campaign_name}/gifts",
    response_model=List[GiftResponse],
    operation_id="find_campaign_gifts",
)
def find_campaign_gifts(campaign_name: str):
    with Session(engine) as session:
        # Get campaign ID
        campaign_result = session.execute(
            text("SELECT id FROM campaigns WHERE name = :name"),
            {"name": campaign_name},
        ).fetchone()
        
        if not campaign_result:
            raise HTTPException(status_code=404, detail=f"Campaign '{campaign_name}' not found")
        
        campaign_id = campaign_result[0]
        
        # Get gifts for this campaign
        gifts_result = session.execute(
            text("""
                SELECT id, constituent_id, campaign_id, fund_id, amount, gift_date, notes
                FROM gifts
                WHERE campaign_id = :campaign_id
                ORDER BY id
            """),
            {"campaign_id": campaign_id},
        ).fetchall()
        
        return [
            GiftResponse(
                id=gift[0],
                constituent_id=gift[1],
                campaign_id=gift[2],
                fund_id=gift[3],
                amount=gift[4],
                gift_date=str(gift[5]),
                notes=gift[6],
            )
            for gift in gifts_result
        ]


@app.get(
    "/gifts",
    response_model=List[GiftResponse],
    operation_id="find_gifts_by_date_and_campaign",
)
def find_gifts_by_date_and_campaign(
    campaign_name: str = Query(...),
    after_date: str = Query(...),
):
    with Session(engine) as session:
        # Get campaign ID
        campaign_result = session.execute(
            text("SELECT id FROM campaigns WHERE name = :name"),
            {"name": campaign_name},
        ).fetchone()
        
        if not campaign_result:
            raise HTTPException(status_code=404, detail=f"Campaign '{campaign_name}' not found")
        
        campaign_id = campaign_result[0]
        
        # Parse date - handle both ISO format and space-separated
        # The after_date is inclusive per spec description
        gifts_result = session.execute(
            text("""
                SELECT id, constituent_id, campaign_id, fund_id, amount, gift_date, notes
                FROM gifts
                WHERE campaign_id = :campaign_id AND gift_date >= :after_date
                ORDER BY id
            """),
            {"campaign_id": campaign_id, "after_date": after_date},
        ).fetchall()
        
        return [
            GiftResponse(
                id=gift[0],
                constituent_id=gift[1],
                campaign_id=gift[2],
                fund_id=gift[3],
                amount=gift[4],
                gift_date=str(gift[5]),
                notes=gift[6],
            )
            for gift in gifts_result
        ]


@app.put(
    "/gifts/{gift_id}",
    response_model=GiftResponse,
    operation_id="reassign_gift_campaign_and_fund",
)
def reassign_gift_campaign_and_fund(gift_id: int, request: ReassignGiftRequest):
    with Session(engine) as session:
        # Get current gift
        gift_result = session.execute(
            text("""
                SELECT id, constituent_id, campaign_id, fund_id, amount, gift_date, notes
                FROM gifts
                WHERE id = :gift_id
            """),
            {"gift_id": gift_id},
        ).fetchone()
        
        if not gift_result:
            raise HTTPException(status_code=404, detail=f"Gift with id {gift_id} not found")
        
        old_campaign_id = gift_result[2]
        old_fund_id = gift_result[3]
        
        # Get new campaign ID
        new_campaign_result = session.execute(
            text("SELECT id FROM campaigns WHERE name = :name"),
            {"name": request.campaign_name},
        ).fetchone()
        
        if not new_campaign_result:
            raise HTTPException(status_code=404, detail=f"Campaign '{request.campaign_name}' not found")
        
        new_campaign_id = new_campaign_result[0]
        
        # Get new fund ID
        new_fund_result = session.execute(
            text("SELECT id FROM funds WHERE name = :name"),
            {"name": request.fund_name},
        ).fetchone()
        
        if not new_fund_result:
            raise HTTPException(status_code=404, detail=f"Fund '{request.fund_name}' not found")
        
        new_fund_id = new_fund_result[0]
        
        # Determine action type for audit log
        campaign_changed = old_campaign_id != new_campaign_id
        fund_changed = old_fund_id != new_fund_id
        
        if campaign_changed and fund_changed:
            action = "reassign_both"
        elif campaign_changed:
            action = "reassign_campaign"
        elif fund_changed:
            action = "reassign_fund"
        else:
            action = "update"
        
        # Update the gift
        session.execute(
            text("""
                UPDATE gifts
                SET campaign_id = :new_campaign_id,
                    fund_id = :new_fund_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :gift_id
            """),
            {
                "new_campaign_id": new_campaign_id,
                "new_fund_id": new_fund_id,
                "gift_id": gift_id,
            },
        )
        
        # Log to audit table
        user_id_str = os.environ.get("AWM_CURRENT_USER_ID", "1")
        try:
            user_id = int(user_id_str)
        except ValueError:
            user_id = 1
        
        session.execute(
            text("""
                INSERT INTO gift_audit_log (
                    gift_id, user_id, action,
                    old_campaign_id, new_campaign_id,
                    old_fund_id, new_fund_id,
                    changed_at, notes
                ) VALUES (
                    :gift_id, :user_id, :action,
                    :old_campaign_id, :new_campaign_id,
                    :old_fund_id, :new_fund_id,
                    CURRENT_TIMESTAMP, :notes
                )
            """),
            {
                "gift_id": gift_id,
                "user_id": user_id,
                "action": action,
                "old_campaign_id": old_campaign_id,
                "new_campaign_id": new_campaign_id,
                "old_fund_id": old_fund_id,
                "new_fund_id": new_fund_id,
                "notes": f"Reassigned from campaign_id={old_campaign_id} to {new_campaign_id}, fund_id={old_fund_id} to {new_fund_id}",
            },
        )
        
        session.commit()
        
        # Return updated gift
        updated_gift = session.execute(
            text("""
                SELECT id, constituent_id, campaign_id, fund_id, amount, gift_date, notes
                FROM gifts
                WHERE id = :gift_id
            """),
            {"gift_id": gift_id},
        ).fetchone()
        
        return GiftResponse(
            id=updated_gift[0],
            constituent_id=updated_gift[1],
            campaign_id=updated_gift[2],
            fund_id=updated_gift[3],
            amount=updated_gift[4],
            gift_date=str(updated_gift[5]),
            notes=updated_gift[6],
        )
