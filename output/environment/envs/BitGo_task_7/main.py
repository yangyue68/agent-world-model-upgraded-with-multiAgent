import os
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, status, Depends
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, CheckConstraint, ForeignKey, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    wallet_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class CustodyPolicy(Base):
    __tablename__ = "custody_policies"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    ofac_screening_enabled = Column(Integer, nullable=False, default=0, 
                                   server_default="0",
                                   comment="CHECK (ofac_screening_enabled IN (0,1))")
    dual_signature_threshold = Column(Float)
    dual_signature_required = Column(Integer, nullable=False, default=0,
                                    server_default="0",
                                    comment="CHECK (dual_signature_required IN (0,1))")
    effective_from = Column(DateTime)
    effective_to = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id"))

class PolicyWallet(Base):
    __tablename__ = "policy_wallets"
    policy_id = Column(Integer, ForeignKey("custody_policies.id"), primary_key=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), primary_key=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=False)

class PolicyApprovalRule(Base):
    __tablename__ = "policy_approval_rules"
    id = Column(Integer, primary_key=True, index=True)
    policy_id = Column(Integer, ForeignKey("custody_policies.id"), nullable=False)
    rule_type = Column(String, nullable=False, 
                      comment="CHECK (rule_type IN ('dual_signature','ofac_screening'))")
    threshold_amount = Column(Float)
    required_signatures = Column(Integer)
    enabled = Column(Integer, nullable=False, default=1,
                     server_default="1",
                     comment="CHECK (enabled IN (0,1))")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PolicyAuditLog(Base):
    __tablename__ = "policy_audit_log"
    id = Column(Integer, primary_key=True, index=True)
    policy_id = Column(Integer, ForeignKey("custody_policies.id"), nullable=False)
    action = Column(String, nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow)
    old_values = Column(Text)
    new_values = Column(Text)

# Pydantic Models
class PolicyWalletResponse(BaseModel):
    wallet_id: int
    wallet_name: str
    wallet_type: str

class UpdateCustodyPolicyRequest(BaseModel):
    ofac_screening_enabled: bool
    dual_signature_threshold: float
    dual_signature_required: bool

class UpdateCustodyPolicyResponse(BaseModel):
    policy_id: int
    name: str
    ofac_screening_enabled: bool
    dual_signature_threshold: float
    dual_signature_required: bool
    effective_from: datetime

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/custody-policies/{policy_id}", response_model=UpdateCustodyPolicyResponse, operation_id="update_custody_policy")
def update_custody_policy(
    policy_id: int,
    policy_update: UpdateCustodyPolicyRequest,
    db = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Get the existing policy
    policy = db.query(CustodyPolicy).filter(CustodyPolicy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    
    # Store old values for audit log
    old_values = {
        "ofac_screening_enabled": bool(policy.ofac_screening_enabled),
        "dual_signature_threshold": policy.dual_signature_threshold,
        "dual_signature_required": bool(policy.dual_signature_required)
    }
    
    # Update policy
    policy.ofac_screening_enabled = int(policy_update.ofac_screening_enabled)
    policy.dual_signature_threshold = policy_update.dual_signature_threshold
    policy.dual_signature_required = int(policy_update.dual_signature_required)
    policy.updated_at = datetime.utcnow()
    policy.updated_by = current_user_id
    
    # Set effective_from to now
    policy.effective_from = datetime.utcnow()
    
    # Create audit log entry
    audit_log = PolicyAuditLog(
        policy_id=policy_id,
        action="updated",
        changed_by=current_user_id,
        old_values=str(old_values),
        new_values=str({
            "ofac_screening_enabled": policy_update.ofac_screening_enabled,
            "dual_signature_threshold": policy_update.dual_signature_threshold,
            "dual_signature_required": policy_update.dual_signature_required
        })
    )
    db.add(audit_log)
    
    db.commit()
    db.refresh(policy)
    
    return UpdateCustodyPolicyResponse(
        policy_id=policy.id,
        name=policy.name,
        ofac_screening_enabled=bool(policy.ofac_screening_enabled),
        dual_signature_threshold=policy.dual_signature_threshold,
        dual_signature_required=bool(policy.dual_signature_required),
        effective_from=policy.effective_from
    )

@app.get("/custody-policies/{policy_id}/wallets", response_model=List[PolicyWalletResponse], operation_id="get_policy_wallets")
def get_policy_wallets(
    policy_id: int,
    db = Depends(get_db)
):
    # Get all wallets associated with the policy
    policy_wallets = db.query(PolicyWallet).filter(PolicyWallet.policy_id == policy_id).all()
    
    wallet_ids = [pw.wallet_id for pw in policy_wallets]
    
    # Get wallet details
    wallets = db.query(Wallet).filter(Wallet.id.in_(wallet_ids)).all()
    
    # Create response
    result = []
    for wallet in wallets:
        result.append(PolicyWalletResponse(
            wallet_id=wallet.id,
            wallet_name=wallet.name,
            wallet_type=wallet.wallet_type
        ))
    
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
