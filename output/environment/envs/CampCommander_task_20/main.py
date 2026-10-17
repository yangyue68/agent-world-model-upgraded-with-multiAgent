import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CancellationPolicyResponse(BaseModel):
    id: int
    rate_plan_id: int
    notice_days: int
    refund_percentage: float
    created_at: str
    updated_at: str


class UpdateCancellationPolicyRequest(BaseModel):
    notice_days: int
    refund_percentage: float


@app.put(
    "/rate_plans/{rate_plan_id}/cancellation_policy",
    response_model=CancellationPolicyResponse,
    operation_id="update_cancellation_policy"
)
def update_cancellation_policy(
    rate_plan_id: int = Path(..., description="Internal ID of the rate plan to update"),
    body: UpdateCancellationPolicyRequest = ...,
):
    """
    Modify the cancellation policy for a specific rate plan to require a specified number of notice days for a full refund.
    This endpoint updates the notice_days and refund_percentage fields in the cancellation_policies table.
    """
    
    # Validate that the rate plan exists
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id FROM rate_plans WHERE id = :rid"), {"rid": rate_plan_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Rate plan not found")

        # Check if a cancellation policy already exists for this rate plan
        result = conn.execute(
            text("SELECT id FROM cancellation_policies WHERE rate_plan_id = :rid"), 
            {"rid": rate_plan_id}
        )
        existing_policy = result.fetchone()

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if existing_policy:
            # Update existing policy
            conn.execute(
                text("""
                    UPDATE cancellation_policies 
                    SET notice_days = :nd, 
                        refund_percentage = :rp, 
                        updated_at = :ua 
                    WHERE rate_plan_id = :rid
                """),
                {
                    "nd": body.notice_days,
                    "rp": body.refund_percentage,
                    "ua": now_str,
                    "rid": rate_plan_id
                }
            )
            conn.commit()
            
            # Fetch the updated record
            result = conn.execute(
                text("""
                    SELECT id, rate_plan_id, notice_days, refund_percentage, created_at, updated_at 
                    FROM cancellation_policies 
                    WHERE rate_plan_id = :rid
                """),
                {"rid": rate_plan_id}
            )
            row = result.fetchone()
        else:
            # Insert new policy
            conn.execute(
                text("""
                    INSERT INTO cancellation_policies (rate_plan_id, notice_days, refund_percentage, created_at, updated_at)
                    VALUES (:rid, :nd, :rp, :ca, :ua)
                """),
                {
                    "rid": rate_plan_id,
                    "nd": body.notice_days,
                    "rp": body.refund_percentage,
                    "ca": now_str,
                    "ua": now_str
                }
            )
            conn.commit()
            
            # Fetch the newly inserted record
            result = conn.execute(
                text("""
                    SELECT id, rate_plan_id, notice_days, refund_percentage, created_at, updated_at 
                    FROM cancellation_policies 
                    WHERE rate_plan_id = :rid
                """),
                {"rid": rate_plan_id}
            )
            row = result.fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve updated policy")

    return CancellationPolicyResponse(
        id=row[0],
        rate_plan_id=row[1],
        notice_days=row[2],
        refund_percentage=float(row[3]),
        created_at=str(row[4]),
        updated_at=str(row[5])
    )


@app.get(
    "/rate_plans/{rate_plan_id}/cancellation_policy",
    response_model=CancellationPolicyResponse,
    operation_id="get_cancellation_policy"
)
def get_cancellation_policy(
    rate_plan_id: int = Path(..., description="Internal ID of the rate plan"),
):
    """
    Fetch the current cancellation policy details for a specific rate plan, including notice days and refund percentage.
    """
    
    with engine.connect() as conn:
        # Verify rate plan exists first
        rp_result = conn.execute(text("SELECT id FROM rate_plans WHERE id = :rid"), {"rid": rate_plan_id})
        if rp_result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Rate plan not found")

        result = conn.execute(
            text("""
                SELECT id, rate_plan_id, notice_days, refund_percentage, created_at, updated_at 
                FROM cancellation_policies 
                WHERE rate_plan_id = :rid
            """),
            {"rid": rate_plan_id}
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Cancellation policy not found for this rate plan")

    return CancellationPolicyResponse(
        id=row[0],
        rate_plan_id=row[1],
        notice_days=row[2],
        refund_percentage=float(row[3]),
        created_at=str(row[4]),
        updated_at=str(row[5])
    )
