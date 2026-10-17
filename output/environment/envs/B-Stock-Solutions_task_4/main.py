import os
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UnpaidWonAuctionItem(BaseModel):
    lot_title: str
    final_bid_amount: float
    auction_close_date: str


@app.get(
    "/my-won-auctions/unpaid",
    response_model=List[UnpaidWonAuctionItem],
    operation_id="list_unpaid_won_auctions",
    tags=["PrimaryResource"],
)
def list_unpaid_won_auctions():
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        return []
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        return []

    query = text("""
        SELECT 
            l.title AS lot_title,
            wa.final_bid_amount AS final_bid_amount,
            a.close_date AS auction_close_date
        FROM won_auctions wa
        JOIN lots l ON wa.lot_id = l.id
        JOIN auctions a ON l.auction_id = a.id
        WHERE wa.user_id = :user_id
          AND wa.payment_status = 'unpaid'
    """)

    with engine.connect() as conn:
        results = conn.execute(query, {"user_id": current_user_id}).fetchall()

    items = []
    for row in results:
        items.append(
            UnpaidWonAuctionItem(
                lot_title=row.lot_title,
                final_bid_amount=float(row.final_bid_amount),
                auction_close_date=str(row.auction_close_date),
            )
        )

    return items
