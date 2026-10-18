import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class SubscriptionPlanSummary(BaseModel):
    id: int
    name: str
    description: str
    billing_interval: str
    price: float


class SubscriptionPlanDetail(BaseModel):
    id: int
    name: str
    description: str
    billing_interval: str
    price: float
    features: List[str]


@app.get("/subscription-plans", response_model=List[SubscriptionPlanSummary], operation_id="find_subscription_plans")
def find_subscription_plans(
    billing_interval: str = Query(..., description="Billing interval filter (must be 'monthly')"),
    max_price: float = Query(..., description="Maximum price filter (must be less than 50)")
):
    if billing_interval != "monthly":
        raise HTTPException(status_code=400, detail="billing_interval must be 'monthly'")
    
    if max_price >= 50:
        raise HTTPException(status_code=400, detail="max_price must be less than 50")

    query = text("""
        SELECT id, name, description, billing_interval, price
        FROM subscription_plans
        WHERE billing_interval = :interval
          AND price < :max_p
          AND is_active = 1
        ORDER BY id ASC
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"interval": billing_interval, "max_p": max_price})
        rows = result.fetchall()
        
        plans = []
        for row in rows:
            plans.append(SubscriptionPlanSummary(
                id=row.id,
                name=row.name,
                description=row.description,
                billing_interval=row.billing_interval,
                price=row.price
            ))
            
    return plans


@app.get("/subscription-plans/{plan_id}", response_model=SubscriptionPlanDetail, operation_id="get_subscription_plan_details")
def get_subscription_plan_details(plan_id: int):
    # Fetch plan details
    plan_query = text("""
        SELECT id, name, description, billing_interval, price
        FROM subscription_plans
        WHERE id = :pid
    """)
    
    with engine.connect() as conn:
        plan_result = conn.execute(plan_query, {"pid": plan_id}).fetchone()
        
        if not plan_result:
            raise HTTPException(status_code=404, detail="Subscription plan not found")
        
        # Fetch features for the plan
        features_query = text("""
            SELECT feature
            FROM plan_features
            WHERE plan_id = :pid
            ORDER BY id ASC
        """)
        
        features_result = conn.execute(features_query, {"pid": plan_id}).fetchall()
        features_list = [f.feature for f in features_result]
        
        return SubscriptionPlanDetail(
            id=plan_result.id,
            name=plan_result.name,
            description=plan_result.description,
            billing_interval=plan_result.billing_interval,
            price=plan_result.price,
            features=features_list
        )
