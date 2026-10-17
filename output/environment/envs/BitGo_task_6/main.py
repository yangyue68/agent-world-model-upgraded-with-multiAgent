import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine: Engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CreateCustodyPolicyRequest(BaseModel):
    name: str
    description: Optional[str] = None
    spending_limit_amount: float
    spending_limit_currency: str
    spending_limit_period_hours: int
    approval_threshold_amount: float
    approval_required_approvers: int
    approval_total_approvers: int
    approval_threshold_currency: str
    time_lock_hours: int
    time_lock_asset: Optional[str] = None
    time_lock_wallet_tag: Optional[str] = None
    approver_user_ids: List[int]
    wallet_tags: List[str]


class CustodyPolicyResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    spending_limit_amount: float
    spending_limit_currency: str
    spending_limit_period_hours: int
    approval_threshold_amount: float
    approval_required_approvers: int
    approval_total_approvers: int
    approval_threshold_currency: str
    time_lock_hours: int
    time_lock_asset: Optional[str]
    time_lock_wallet_tag: Optional[str]
    created_by: int
    created_at: str
    updated_at: str


@app.post(
    "/custody-policies",
    response_model=CustodyPolicyResponse,
    operation_id="create_custody_policy",
    summary="Create a new custody policy with specified spending limits, approval requirements, and time locks",
    tags=["PrimaryResource"],
)
def create_custody_policy(request: CreateCustodyPolicyRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    # Validate that all approver_user_ids exist in the users table
    with engine.connect() as conn:
        if request.approver_user_ids:
            placeholders = ", ".join([f":id_{i}" for i in range(len(request.approver_user_ids))])
            query = f"SELECT id FROM users WHERE id IN ({placeholders})"
            params = {f"id_{i}": uid for i, uid in enumerate(request.approver_user_ids)}
            result = conn.execute(text(query), params)
            existing_user_ids = {row[0] for row in result.fetchall()}
            
            missing_users = set(request.approver_user_ids) - existing_user_ids
            if missing_users:
                raise HTTPException(
                    status_code=400,
                    detail=f"Approver user IDs not found: {sorted(missing_users)}"
                )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        # Insert into custody_policies
        insert_policy_sql = text("""
            INSERT INTO custody_policies (
                name, description, spending_limit_amount, spending_limit_currency,
                spending_limit_period_hours, approval_threshold_amount,
                approval_required_approvers, approval_total_approvers,
                approval_threshold_currency, time_lock_hours, time_lock_asset,
                time_lock_wallet_tag, created_by, created_at, updated_at
            ) VALUES (
                :name, :description, :spending_limit_amount, :spending_limit_currency,
                :spending_limit_period_hours, :approval_threshold_amount,
                :approval_required_approvers, :approval_total_approvers,
                :approval_threshold_currency, :time_lock_hours, :time_lock_asset,
                :time_lock_wallet_tag, :created_by, :created_at, :updated_at
            )
        """)
        
        params = {
            "name": request.name,
            "description": request.description,
            "spending_limit_amount": request.spending_limit_amount,
            "spending_limit_currency": request.spending_limit_currency,
            "spending_limit_period_hours": request.spending_limit_period_hours,
            "approval_threshold_amount": request.approval_threshold_amount,
            "approval_required_approvers": request.approval_required_approvers,
            "approval_total_approvers": request.approval_total_approvers,
            "approval_threshold_currency": request.approval_threshold_currency,
            "time_lock_hours": request.time_lock_hours,
            "time_lock_asset": request.time_lock_asset,
            "time_lock_wallet_tag": request.time_lock_wallet_tag,
            "created_by": current_user_id,
            "created_at": now,
            "updated_at": now,
        }
        
        try:
            result = conn.execute(insert_policy_sql, params)
            policy_id = result.lastrowid
        except Exception as e:
            error_msg = str(e)
            if "UNIQUE constraint failed: custody_policies.name" in error_msg:
                raise HTTPException(status_code=409, detail=f"Custody policy with name '{request.name}' already exists")
            elif "CHECK constraint failed" in error_msg:
                raise HTTPException(status_code=400, detail=f"Validation error: {error_msg}")
            else:
                raise HTTPException(status_code=500, detail=f"Database error: {error_msg}")

        # Insert into policy_approvers
        if request.approver_user_ids:
            insert_approver_sql = text("""
                INSERT INTO policy_approvers (policy_id, user_id, created_at)
                VALUES (:policy_id, :user_id, :created_at)
            """)
            for user_id in request.approver_user_ids:
                conn.execute(insert_approver_sql, {
                    "policy_id": policy_id,
                    "user_id": user_id,
                    "created_at": now,
                })

        # Insert into policy_wallet_tags
        if request.wallet_tags:
            insert_tag_sql = text("""
                INSERT INTO policy_wallet_tags (policy_id, tag, created_at)
                VALUES (:policy_id, :tag, :created_at)
            """)
            for tag in request.wallet_tags:
                conn.execute(insert_tag_sql, {
                    "policy_id": policy_id,
                    "tag": tag,
                    "created_at": now,
                })

        # Fetch the created policy to return
        fetch_sql = text("""
            SELECT id, name, description, spending_limit_amount, spending_limit_currency,
                   spending_limit_period_hours, approval_threshold_amount,
                   approval_required_approvers, approval_total_approvers,
                   approval_threshold_currency, time_lock_hours, time_lock_asset,
                   time_lock_wallet_tag, created_by, created_at, updated_at
            FROM custody_policies
            WHERE id = :policy_id
        """)
        row = conn.execute(fetch_sql, {"policy_id": policy_id}).fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve created policy")

    return CustodyPolicyResponse(
        id=row[0],
        name=row[1],
        description=row[2],
        spending_limit_amount=row[3],
        spending_limit_currency=row[4],
        spending_limit_period_hours=row[5],
        approval_threshold_amount=row[6],
        approval_required_approvers=row[7],
        approval_total_approvers=row[8],
        approval_threshold_currency=row[9],
        time_lock_hours=row[10],
        time_lock_asset=row[11],
        time_lock_wallet_tag=row[12],
        created_by=row[13],
        created_at=str(row[14]),
        updated_at=str(row[15]),
    )
