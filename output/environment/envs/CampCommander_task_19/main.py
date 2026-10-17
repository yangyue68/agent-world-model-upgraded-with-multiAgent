import os
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AffectedSite(BaseModel):
    site_id: int
    site_name: str


class DeactivateResponse(BaseModel):
    message: str
    affected_sites: List[AffectedSite]


@app.put("/rate-plans/{rate_plan_id}/deactivate", response_model=DeactivateResponse, operation_id="deactivate_rate_plan_for_all_sites")
def deactivate_rate_plan_for_all_sites(rate_plan_id: int):
    """
    Deactivates the specified rate plan for all sites starting April 1, 2024.
    This updates the site_rate_plans table to set is_active=0 for all entries 
    matching the rate_plan_id where effective_from >= '2024-04-01'.
    """
    
    # First, verify the rate plan exists and get its name for context if needed
    with engine.connect() as conn:
        rp_check = conn.execute(
            text("SELECT id, name FROM rate_plans WHERE id = :rp_id"),
            {"rp_id": rate_plan_id}
        ).fetchone()
        
        if not rp_check:
            raise HTTPException(status_code=404, detail="Rate plan not found")

        # Identify the rows in site_rate_plans that match the criteria:
        # 1. rate_plan_id matches
        # 2. effective_from >= '2024-04-01'
        # We need to return the site_id and site_name of these affected records.
        
        query_select_affected = text("""
            SELECT DISTINCT s.id AS site_id, s.name AS site_name
            FROM site_rate_plans srp
            JOIN sites s ON srp.site_id = s.id
            WHERE srp.rate_plan_id = :rp_id
              AND srp.effective_from >= '2024-04-01'
        """)
        
        affected_rows = conn.execute(query_select_affected, {"rp_id": rate_plan_id}).fetchall()
        
        affected_sites_list = [
            AffectedSite(site_id=row[0], site_name=row[1])
            for row in affected_rows
        ]

        # Perform the update
        # Set is_active = 0 for all entries matching the rate_plan_id where effective_from >= '2024-04-01'
        update_query = text("""
            UPDATE site_rate_plans
            SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE rate_plan_id = :rp_id
              AND effective_from >= '2024-04-01'
        """)
        
        result = conn.execute(update_query, {"rp_id": rate_plan_id})
        conn.commit()

        count_updated = result.rowcount
        
        message = f"Successfully deactivated rate plan {rate_plan_id} for {len(affected_sites_list)} sites."
        if len(affected_sites_list) == 0:
             message = f"No active assignments found for rate plan {rate_plan_id} starting on or after April 1, 2024."

        return DeactivateResponse(
            message=message,
            affected_sites=affected_sites_list
        )
