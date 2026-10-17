import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class CustodyPolicyResponse(BaseModel):
    id: int
    name: str
    description: str
    status: str


class PolicySummary(BaseModel):
    id: int
    name: str


class WalletSummary(BaseModel):
    id: int
    name: str


class AssignmentCheckResponse(BaseModel):
    is_assigned: bool


class DeleteResponse(BaseModel):
    message: str


@app.get("/custody_policies/{policy_id}", response_model=CustodyPolicyResponse, operation_id="get_custody_policy")
def get_custody_policy(policy_id: int = Path(...)):
    with SessionLocal() as db:
        result = db.execute(
            text("SELECT id, name, description, status FROM custody_policies WHERE id = :id"),
            {"id": policy_id}
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Custody policy not found")
        return CustodyPolicyResponse(
            id=result[0],
            name=result[1],
            description=result[2] or "",
            status=result[3]
        )


@app.get("/custody_policies", response_model=List[PolicySummary], operation_id="find_custody_policies")
def find_custody_policies(name: str = Query(...)):
    with SessionLocal() as db:
        results = db.execute(
            text("SELECT id, name FROM custody_policies WHERE name LIKE :name"),
            {"name": f"%{name}%"}
        ).fetchall()
        return [PolicySummary(id=r[0], name=r[1]) for r in results]


@app.get("/wallets", response_model=List[WalletSummary], operation_id="find_active_wallets")
def find_active_wallets():
    with SessionLocal() as db:
        results = db.execute(
            text("SELECT id, name FROM wallets WHERE status = 'active'")
        ).fetchall()
        return [WalletSummary(id=r[0], name=r[1]) for r in results]


@app.get("/wallet_policies", response_model=AssignmentCheckResponse, operation_id="check_policy_assignment")
def check_policy_assignment(policy_id: int = Query(...)):
    with SessionLocal() as db:
        result = db.execute(
            text("""
                SELECT COUNT(*) 
                FROM wallet_policies wp
                JOIN wallets w ON wp.wallet_id = w.id
                WHERE wp.policy_id = :policy_id AND w.status = 'active'
            """),
            {"policy_id": policy_id}
        ).fetchone()
        count = result[0] if result else 0
        return AssignmentCheckResponse(is_assigned=count > 0)


@app.delete("/custody_policies/{policy_id}", response_model=DeleteResponse, operation_id="delete_inactive_policy")
def delete_inactive_policy(
    policy_id: int = Path(...),
    reason: str = Query(...)
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=500, detail="Current user ID not available")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID format")

    with SessionLocal() as db:
        # Get the policy to verify it exists and is inactive
        policy_result = db.execute(
            text("SELECT id, name, status FROM custody_policies WHERE id = :id"),
            {"id": policy_id}
        ).fetchone()
        
        if not policy_result:
            raise HTTPException(status_code=404, detail="Custody policy not found")
        
        policy_name = policy_result[1]
        policy_status = policy_result[2]
        
        # Verify the policy is inactive
        if policy_status != "inactive":
            raise HTTPException(status_code=400, detail="Only inactive policies can be deleted")
        
        # Check if the policy is assigned to any active wallet
        assignment_check = db.execute(
            text("""
                SELECT COUNT(*) 
                FROM wallet_policies wp
                JOIN wallets w ON wp.wallet_id = w.id
                WHERE wp.policy_id = :policy_id AND w.status = 'active'
            """),
            {"policy_id": policy_id}
        ).fetchone()
        
        assignment_count = assignment_check[0] if assignment_check else 0
        
        if assignment_count > 0:
            raise HTTPException(status_code=400, detail="Cannot delete policy assigned to active wallet(s)")
        
        # Delete the policy
        db.execute(
            text("DELETE FROM custody_policies WHERE id = :id"),
            {"id": policy_id}
        )
        
        # Create audit log entry
        db.execute(
            text("""
                INSERT INTO audit_logs (event_type, entity_type, entity_id, action, reason, performed_by, details)
                VALUES (:event_type, :entity_type, :entity_id, :action, :reason, :performed_by, :details)
            """),
            {
                "event_type": "policy_deletion",
                "entity_type": "custody_policy",
                "entity_id": policy_id,
                "action": "deleted",
                "reason": reason,
                "performed_by": current_user_id,
                "details": f"Deleted inactive custody policy '{policy_name}' after confirming no active wallet assignments."
            }
        )
        
        db.commit()
        
        return DeleteResponse(message=f"Custody policy '{policy_name}' deleted successfully.")
