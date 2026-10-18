import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class StartOnboardingFlowResponse(BaseModel):
    flow_id: int
    user_id: int
    flow_type: str
    status: str
    started_at: str


class SelectAccountTypeResponse(BaseModel):
    flow_id: int
    account_type_code: str
    status: str
    updated_at: str


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")


@app.post(
    "/onboarding-flows",
    response_model=StartOnboardingFlowResponse,
    operation_id="start_onboarding_flow",
)
def start_onboarding_flow():
    user_id = get_current_user_id()

    with engine.connect() as conn:
        # Verify user exists
        result = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        flow_type = "chase_you_invest_self_directed"
        status = "started"

        result = conn.execute(
            text(
                """
                INSERT INTO onboarding_flows (user_id, flow_type, status, started_at, created_at, updated_at)
                VALUES (:user_id, :flow_type, :status, :now, :now, :now)
                """
            ),
            {
                "user_id": user_id,
                "flow_type": flow_type,
                "status": status,
                "now": now,
            },
        )
        flow_id = result.lastrowid

        # Create initial welcome step
        conn.execute(
            text(
                """
                INSERT INTO onboarding_steps (flow_id, step_name, step_order, status, started_at, created_at, updated_at)
                VALUES (:flow_id, 'welcome', 1, 'in_progress', :now, :now, :now)
                """
            ),
            {"flow_id": flow_id, "now": now},
        )

        conn.commit()

    return StartOnboardingFlowResponse(
        flow_id=flow_id,
        user_id=user_id,
        flow_type=flow_type,
        status=status,
        started_at=now,
    )


@app.put(
    "/onboarding-flows/{flow_id}/account-type",
    response_model=SelectAccountTypeResponse,
    operation_id="select_account_type",
)
def select_account_type(
    flow_id: int,
    account_type_code: str = Query(..., description="The code of the selected account type"),
):
    user_id = get_current_user_id()

    with engine.connect() as conn:
        # Verify flow exists and belongs to user
        result = conn.execute(
            text("SELECT id, user_id, status FROM onboarding_flows WHERE id = :fid"),
            {"fid": flow_id},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Onboarding flow not found")
        if row.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Verify account type exists
        result = conn.execute(
            text("SELECT id, code FROM account_types WHERE code = :code"),
            {"code": account_type_code},
        )
        at_row = result.fetchone()
        if at_row is None:
            raise HTTPException(status_code=404, detail="Account type not found")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Update the onboarding flow status to in_progress and set current_step
        conn.execute(
            text(
                """
                UPDATE onboarding_flows
                SET status = 'in_progress', current_step = 'account_type_selection', updated_at = :now
                WHERE id = :fid
                """
            ),
            {"fid": flow_id, "now": now},
        )

        # Check if account_type_selection step already exists
        result = conn.execute(
            text(
                """
                SELECT id, status FROM onboarding_steps
                WHERE flow_id = :fid AND step_name = 'account_type_selection'
                """
            ),
            {"fid": flow_id},
        )
        step_row = result.fetchone()

        if step_row is None:
            # Insert new step
            conn.execute(
                text(
                    """
                    INSERT INTO onboarding_steps (flow_id, step_name, step_order, status, data, started_at, completed_at, created_at, updated_at)
                    VALUES (:fid, 'account_type_selection', 2, 'completed', :data, :now, :now, :now, :now)
                    """
                ),
                {
                    "fid": flow_id,
                    "data": f'{{"account_type_code":"{account_type_code}"}}',
                    "now": now,
                },
            )
        else:
            # Update existing step
            conn.execute(
                text(
                    """
                    UPDATE onboarding_steps
                    SET status = 'completed', data = :data, completed_at = :now, updated_at = :now
                    WHERE id = :sid
                    """
                ),
                {
                    "sid": step_row.id,
                    "data": f'{{"account_type_code":"{account_type_code}"}}',
                    "now": now,
                },
            )

        conn.commit()

    return SelectAccountTypeResponse(
        flow_id=flow_id,
        account_type_code=account_type_code,
        status="in_progress",
        updated_at=now,
    )
