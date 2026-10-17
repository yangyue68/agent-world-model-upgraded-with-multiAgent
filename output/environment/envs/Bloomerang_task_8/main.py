import os
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class BulkUpdateResponse(BaseModel):
    updated_count: int


@app.put(
    "/gifts/bulk-update",
    response_model=BulkUpdateResponse,
    operation_id="bulk_update_unreconciled_gifts",
    summary="Bulk update unreconciled gifts with no campaign or fund assigned",
    description="Update all unreconciled gifts logged between October 1–15, 2024, that currently have no campaign or fund assigned, to default to Campaign 'Year-End Appeal 2024' and Fund 'Unrestricted Gifts'.",
)
def bulk_update_unreconciled_gifts(
    start_date: str = Query(..., description="Start date for filtering gifts (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date for filtering gifts (YYYY-MM-DD)"),
    campaign_name: str = Query(..., description="Name of the campaign to assign"),
    fund_name: str = Query(..., description="Name of the fund to assign"),
):
    """
    Bulk update unreconciled gifts within a date range that have no campaign or fund assigned.
    """
    # Validate dates format (basic check)
    try:
        from datetime import datetime
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")

    with engine.connect() as conn:
        # Find campaign ID by name
        campaign_result = conn.execute(
            text("SELECT id FROM campaigns WHERE name = :name"),
            {"name": campaign_name}
        ).fetchone()
        if not campaign_result:
            raise HTTPException(status_code=404, detail=f"Campaign '{campaign_name}' not found")
        campaign_id = campaign_result[0]

        # Find fund ID by name
        fund_result = conn.execute(
            text("SELECT id FROM funds WHERE name = :name"),
            {"name": fund_name}
        ).fetchone()
        if not fund_result:
            raise HTTPException(status_code=404, detail=f"Fund '{fund_name}' not found")
        fund_id = fund_result[0]

        # Get current user ID from environment
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")

        # Update gifts that are unreconciled, have no campaign, no fund, and fall within date range
        result = conn.execute(
            text("""
                UPDATE gifts
                SET campaign_id = :campaign_id,
                    fund_id = :fund_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE gift_date >= :start_date
                  AND gift_date <= :end_date
                  AND reconciled = 0
                  AND campaign_id IS NULL
                  AND fund_id IS NULL
            """),
            {
                "campaign_id": campaign_id,
                "fund_id": fund_id,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        conn.commit()

        updated_count = result.rowcount

    return BulkUpdateResponse(updated_count=updated_count)
