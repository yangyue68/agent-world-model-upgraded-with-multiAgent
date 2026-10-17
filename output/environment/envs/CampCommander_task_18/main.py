import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ExclusionCreate(BaseModel):
    discount_id: int
    rate_plan_id: int


class ExclusionResponse(BaseModel):
    id: int
    discount_id: int
    rate_plan_id: int
    created_at: str


class DeleteResponse(BaseModel):
    message: str


def _parse_dt(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


@app.post("/discount-rate-plan-exclusions", response_model=ExclusionResponse, operation_id="create_discount_rate_plan_exclusion")
def create_discount_rate_plan_exclusion(payload: ExclusionCreate):
    with engine.begin() as conn:
        # Verify discount exists
        row = conn.execute(
            text("SELECT id FROM discounts WHERE id = :did"),
            {"did": payload.discount_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Discount not found")

        # Verify rate plan exists
        row = conn.execute(
            text("SELECT id FROM rate_plans WHERE id = :rpid"),
            {"rpid": payload.rate_plan_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Rate plan not found")

        # Check for existing exclusion (UNIQUE constraint on discount_id, rate_plan_id)
        existing = conn.execute(
            text(
                "SELECT id FROM discount_rate_plan_exclusions "
                "WHERE discount_id = :did AND rate_plan_id = :rpid"
            ),
            {"did": payload.discount_id, "rpid": payload.rate_plan_id},
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Exclusion already exists")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text(
                "INSERT INTO discount_rate_plan_exclusions (discount_id, rate_plan_id, created_at) "
                "VALUES (:did, :rpid, :created_at)"
            ),
            {
                "did": payload.discount_id,
                "rpid": payload.rate_plan_id,
                "created_at": now,
            },
        )
        new_id = result.lastrowid

        row = conn.execute(
            text(
                "SELECT id, discount_id, rate_plan_id, created_at "
                "FROM discount_rate_plan_exclusions WHERE id = :id"
            ),
            {"id": new_id},
        ).fetchone()

    return ExclusionResponse(
        id=row[0],
        discount_id=row[1],
        rate_plan_id=row[2],
        created_at=_parse_dt(row[3]),
    )


@app.get("/discount-rate-plan-exclusions", response_model=list[ExclusionResponse], operation_id="find_discount_rate_plan_exclusions")
def find_discount_rate_plan_exclusions():
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, discount_id, rate_plan_id, created_at "
                "FROM discount_rate_plan_exclusions ORDER BY id"
            )
        ).fetchall()

    return [
        ExclusionResponse(
            id=r[0],
            discount_id=r[1],
            rate_plan_id=r[2],
            created_at=_parse_dt(r[3]),
        )
        for r in rows
    ]


@app.delete("/discount-rate-plan-exclusions/{id}", response_model=DeleteResponse, operation_id="delete_discount_rate_plan_exclusion")
def delete_discount_rate_plan_exclusion(id: int):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM discount_rate_plan_exclusions WHERE id = :id"),
            {"id": id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Exclusion not found")

        conn.execute(
            text("DELETE FROM discount_rate_plan_exclusions WHERE id = :id"),
            {"id": id},
        )

    return DeleteResponse(message="Exclusion deleted successfully")
