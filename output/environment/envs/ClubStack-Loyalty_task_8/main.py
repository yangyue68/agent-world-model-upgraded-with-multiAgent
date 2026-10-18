import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RewardCreate(BaseModel):
    name: str = Field(..., description="Name of the reward")
    point_cost: int = Field(..., ge=0, description="Point cost required to redeem the reward")
    discount_value: float = Field(..., ge=0, description="Monetary discount value of the reward")
    description: str | None = Field(None, description="Optional description of the reward")


class RewardResponse(BaseModel):
    id: int
    name: str
    point_cost: int
    discount_value: float
    description: str | None
    is_active: int
    created_at: str
    updated_at: str


@app.post("/rewards", response_model=RewardResponse, operation_id="create_reward")
def create_reward(payload: RewardCreate):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO rewards (name, point_cost, discount_value, description, is_active, created_at, updated_at)
                VALUES (:name, :point_cost, :discount_value, :description, 1, :now, :now)
                """
            ),
            {
                "name": payload.name,
                "point_cost": payload.point_cost,
                "discount_value": payload.discount_value,
                "description": payload.description,
                "now": now,
            },
        )
        reward_id = result.lastrowid

        row = conn.execute(
            text(
                """
                SELECT id, name, point_cost, discount_value, description, is_active, created_at, updated_at
                FROM rewards
                WHERE id = :reward_id
                """
            ),
            {"reward_id": reward_id},
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve created reward.")

    return RewardResponse(
        id=row.id,
        name=row.name,
        point_cost=row.point_cost,
        discount_value=float(row.discount_value),
        description=row.description,
        is_active=row.is_active,
        created_at=str(row.created_at),
        updated_at=str(row.updated_at),
    )
