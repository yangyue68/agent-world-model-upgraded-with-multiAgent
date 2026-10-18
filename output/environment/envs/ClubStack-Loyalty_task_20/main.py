import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class TierInput(BaseModel):
    name: str
    monthly_price: float
    trial_days: int
    annual_discount_percent: Optional[float] = 0.0


class CreatePlanRequest(BaseModel):
    name: str
    tiers: List[TierInput]


class CreatePlanResponse(BaseModel):
    plan_id: int
    tier_ids: List[int]


@app.post("/subscription_plans", response_model=CreatePlanResponse, operation_id="create_gold_fitness_plan")
def create_subscription_plan(request: CreatePlanRequest):
    db = SessionLocal()
    try:
        # Check if plan with this name already exists to avoid UNIQUE constraint violation on subscription_plans.name
        existing_plan = db.execute(
            text("SELECT id FROM subscription_plans WHERE name = :name"),
            {"name": request.name}
        ).fetchone()
        
        if existing_plan:
            raise HTTPException(status_code=409, detail=f"Subscription plan '{request.name}' already exists.")

        # Insert the new plan
        result = db.execute(
            text("""
                INSERT INTO subscription_plans (name, description)
                VALUES (:name, :description)
            """),
            {
                "name": request.name,
                "description": f"Auto-generated plan for {request.name}"
            }
        )
        plan_id = result.lastrowid
        
        tier_ids = []
        
        # Insert each tier associated with the new plan
        for tier in request.tiers:
            # Ensure default values are applied correctly if None was passed or omitted
            discount = tier.annual_discount_percent if tier.annual_discount_percent is not None else 0.0
            
            tier_result = db.execute(
                text("""
                    INSERT INTO subscription_tiers (plan_id, name, monthly_price, trial_days, annual_discount_percent)
                    VALUES (:plan_id, :name, :monthly_price, :trial_days, :annual_discount_percent)
                """),
                {
                    "plan_id": plan_id,
                    "name": tier.name,
                    "monthly_price": tier.monthly_price,
                    "trial_days": tier.trial_days,
                    "annual_discount_percent": discount
                }
            )
            tier_ids.append(tier_result.lastrowid)
            
        db.commit()
        
        return CreatePlanResponse(plan_id=plan_id, tier_ids=tier_ids)
    
    except Exception as e:
        db.rollback()
        # Re-raise HTTP exceptions directly
        if isinstance(e, HTTPException):
            raise e
        # Wrap other database errors into a generic server error or specific conflict if needed
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
