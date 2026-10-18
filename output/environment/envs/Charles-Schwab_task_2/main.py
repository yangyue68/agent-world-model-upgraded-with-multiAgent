import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class SetDefaultResponse(BaseModel):
    account_id: int
    is_default_for_automated_investments: bool


class AssignPortfolioModelRequest(BaseModel):
    portfolio_model_id: int


class AssignPortfolioModelResponse(BaseModel):
    account_id: int
    portfolio_model_id: int


@app.put(
    "/accounts/{account_id}/default_for_automated_investments",
    response_model=SetDefaultResponse,
    operation_id="set_account_as_default_for_automated_investments",
    tags=["PrimaryResource"],
)
def set_account_as_default_for_automated_investments(account_id: int = Path(...)):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with engine.connect() as conn:
        # Verify the account exists and belongs to the current user
        result = conn.execute(
            text("SELECT id, user_id FROM accounts WHERE id = :account_id"),
            {"account_id": account_id},
        ).fetchone()

        if result is None:
            raise HTTPException(status_code=404, detail="Account not found")

        if result.user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Account does not belong to current user")

        # Unset any previously designated default account for this user
        conn.execute(
            text(
                "UPDATE accounts SET is_default_for_automated_investments = 0, updated_at = CURRENT_TIMESTAMP "
                "WHERE user_id = :user_id AND is_default_for_automated_investments = 1"
            ),
            {"user_id": current_user_id},
        )

        # Set the specified account as default
        conn.execute(
            text(
                "UPDATE accounts SET is_default_for_automated_investments = 1, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = :account_id"
            ),
            {"account_id": account_id},
        )

        conn.commit()

    return SetDefaultResponse(
        account_id=account_id,
        is_default_for_automated_investments=True,
    )


@app.put(
    "/accounts/{account_id}/portfolio_model",
    response_model=AssignPortfolioModelResponse,
    operation_id="assign_portfolio_model_to_account",
    tags=["PrimaryResource"],
)
def assign_portfolio_model_to_account(
    account_id: int = Path(...),
    body: AssignPortfolioModelRequest = Body(...),
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    portfolio_model_id = body.portfolio_model_id

    with engine.connect() as conn:
        # Verify the account exists and belongs to the current user
        account_result = conn.execute(
            text("SELECT id, user_id FROM accounts WHERE id = :account_id"),
            {"account_id": account_id},
        ).fetchone()

        if account_result is None:
            raise HTTPException(status_code=404, detail="Account not found")

        if account_result.user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Account does not belong to current user")

        # Verify the portfolio model exists
        model_result = conn.execute(
            text("SELECT id FROM portfolio_models WHERE id = :model_id"),
            {"model_id": portfolio_model_id},
        ).fetchone()

        if model_result is None:
            raise HTTPException(status_code=404, detail="Portfolio model not found")

        # Check if there's an existing assignment for this account
        existing_assignment = conn.execute(
            text("SELECT id FROM account_portfolio_assignments WHERE account_id = :account_id"),
            {"account_id": account_id},
        ).fetchone()

        if existing_assignment:
            # Update existing assignment
            conn.execute(
                text(
                    "UPDATE account_portfolio_assignments "
                    "SET portfolio_model_id = :model_id, updated_at = CURRENT_TIMESTAMP "
                    "WHERE account_id = :account_id"
                ),
                {"model_id": portfolio_model_id, "account_id": account_id},
            )
        else:
            # Insert new assignment
            conn.execute(
                text(
                    "INSERT INTO account_portfolio_assignments (account_id, portfolio_model_id, assigned_at, updated_at) "
                    "VALUES (:account_id, :model_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"account_id": account_id, "model_id": portfolio_model_id},
            )

        conn.commit()

    return AssignPortfolioModelResponse(
        account_id=account_id,
        portfolio_model_id=portfolio_model_id,
    )
