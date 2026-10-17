import os
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UpdateGrantRoundRequest(BaseModel):
    new_submission_limit: int
    new_end_date: str


class GrantRoundResponse(BaseModel):
    id: int
    name: str
    submission_limit: int
    end_date: str


def parse_datetime(dt_str: str) -> datetime:
    # Handle both ISO-8601 with T and space-separated formats
    dt_str = dt_str.strip()
    if 'T' in dt_str:
        # Replace Z with +00:00 for compatibility
        if dt_str.endswith('Z'):
            dt_str = dt_str[:-1] + '+00:00'
        try:
            return datetime.fromisoformat(dt_str)
        except ValueError:
            pass
    # Try common formats
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unable to parse datetime: {dt_str}")


@app.put("/grant_rounds/{round_id}", response_model=GrantRoundResponse, operation_id="update_grant_round_config")
def update_grant_round_config(
    round_id: int = Path(..., description="Internal ID of the grant round to update"),
    body: UpdateGrantRoundRequest = None,
):
    if body is None:
        raise HTTPException(status_code=422, detail="Request body required")

    # Get current user from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="No authenticated user")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Fetch current round data
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, name, submission_limit, end_date FROM grant_rounds WHERE id = :rid"),
            {"rid": round_id},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Grant round not found")

        old_submission_limit = row[2]
        old_end_date_str = row[3]

        # Parse old end date
        try:
            old_end_date = parse_datetime(old_end_date_str)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid stored end date")

        # Parse new end date
        try:
            new_end_date = parse_datetime(body.new_end_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid new_end_date format")

        # Format new end date for storage (SQLite DATETIME expects YYYY-MM-DD HH:MM:SS)
        new_end_date_str = new_end_date.strftime("%Y-%m-%d %H:%M:%S")

        # Update the grant round
        conn.execute(
            text("""
                UPDATE grant_rounds 
                SET submission_limit = :new_limit, 
                    end_date = :new_end_date,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :rid
            """),
            {
                "new_limit": body.new_submission_limit,
                "new_end_date": new_end_date_str,
                "rid": round_id,
            },
        )

        # Record change in history
        conn.execute(
            text("""
                INSERT INTO grant_round_config_history 
                (round_id, old_submission_limit, new_submission_limit, old_end_date, new_end_date, changed_by)
                VALUES (:rid, :old_limit, :new_limit, :old_end, :new_end, :changed_by)
            """),
            {
                "rid": round_id,
                "old_limit": old_submission_limit,
                "new_limit": body.new_submission_limit,
                "old_end": old_end_date_str,
                "new_end": new_end_date_str,
                "changed_by": current_user_id,
            },
        )

        conn.commit()

        # Fetch updated data
        result = conn.execute(
            text("SELECT id, name, submission_limit, end_date FROM grant_rounds WHERE id = :rid"),
            {"rid": round_id},
        )
        updated_row = result.fetchone()

        return GrantRoundResponse(
            id=updated_row[0],
            name=updated_row[1],
            submission_limit=updated_row[2],
            end_date=updated_row[3],
        )


@app.get("/grant_rounds/{round_id}", response_model=GrantRoundResponse, operation_id="get_grant_round_config")
def get_grant_round_config(
    round_id: int = Path(..., description="Internal ID of the grant round to retrieve"),
):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, name, submission_limit, end_date FROM grant_rounds WHERE id = :rid"),
            {"rid": round_id},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Grant round not found")

        return GrantRoundResponse(
            id=row[0],
            name=row[1],
            submission_limit=row[2],
            end_date=row[3],
        )
