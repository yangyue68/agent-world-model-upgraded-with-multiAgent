import os
from datetime import datetime, timedelta

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PointsRedeemedSummaryItem(BaseModel):
    reward_id: int
    tier: str
    total_points_redeemed: int


@app.get(
    "/reports/points-redeemed-summary",
    response_model=list[PointsRedeemedSummaryItem],
    operation_id="get_points_redeemed_summary",
    summary="Get total points redeemed in the last 30 days, grouped by reward ID and member tier",
    description="Generates a report showing the total points redeemed for each reward within the last 30 days, grouped by the member tier of the redeemer. Only active rewards are considered.",
    tags=["PrimaryResource"],
)
def get_points_redeemed_summary():
    """
    Get total points redeemed in the last 30 days, grouped by reward ID and member tier.
    Only active rewards are considered.
    """
    # Calculate the date 30 days ago from now
    cutoff_date = datetime.utcnow() - timedelta(days=30)
    
    query = text("""
        SELECT 
            r.id AS reward_id,
            m.tier AS tier,
            SUM(red.points_redeemed) AS total_points_redeemed
        FROM redemptions red
        JOIN members m ON red.member_id = m.id
        JOIN rewards r ON red.reward_id = r.id
        WHERE red.redeemed_at >= :cutoff_date
          AND r.is_active = 1
        GROUP BY r.id, m.tier
        ORDER BY r.id ASC, m.tier ASC
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"cutoff_date": cutoff_date})
        rows = result.fetchall()
        
        items = []
        for row in rows:
            items.append({
                "reward_id": row[0],
                "tier": row[1],
                "total_points_redeemed": row[2]
            })
            
    return items
