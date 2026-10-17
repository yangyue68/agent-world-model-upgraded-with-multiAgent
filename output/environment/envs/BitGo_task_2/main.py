import os
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    CheckConstraint,
    func,
    case,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    label = Column(String, nullable=False)
    wallet_type = Column(String, nullable=False)
    blockchain = Column(String, nullable=False)
    address = Column(String, unique=True)
    bip_standard = Column(String)
    status = Column(String, default="active")
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SpendingPolicy(Base):
    __tablename__ = "spending_policies"
    id = Column(Integer, primary_key=True, index=True)
    wallet_id = Column(Integer, unique=True, nullable=False)
    required_approvals = Column(Integer, nullable=False)
    total_approvers = Column(Integer, nullable=False)
    block_sanctioned_addresses = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WalletApprover(Base):
    __tablename__ = "wallet_approvers"
    wallet_id = Column(Integer, primary_key=True)
    user_id = Column(Integer, primary_key=True)
    approval_order = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)


class WalletAccess(Base):
    __tablename__ = "wallet_access"
    id = Column(Integer, primary_key=True, index=True)
    wallet_id = Column(Integer, nullable=False)
    role_id = Column(Integer, nullable=False)
    access_level = Column(String, nullable=False)
    granted_by = Column(Integer, nullable=False)
    granted_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime)


class SanctionedAddress(Base):
    __tablename__ = "sanctioned_addresses"
    id = Column(Integer, primary_key=True, index=True)
    address = Column(String, unique=True, nullable=False)
    source = Column(String, nullable=False)
    reason = Column(Text)
    listed_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Request models
class FindRolesRequest(BaseModel):
    name: str


class CreateColdWalletRequest(BaseModel):
    label: str
    blockchain: str
    bip_standard: str


class ConfigureSpendingPolicyRequest(BaseModel):
    wallet_id: int
    required_approvals: int
    total_approvers: int
    block_sanctioned_addresses: bool


class AssignApproversRequest(BaseModel):
    wallet_id: int
    user_ids: List[int]


class GrantReadonlyAccessRequest(BaseModel):
    wallet_id: int
    role_name: str
    access_level: str


# Response models
class RoleResponse(BaseModel):
    id: int
    name: str


class WalletResponse(BaseModel):
    id: int
    label: str
    blockchain: str
    bip_standard: str
    status: str


class SpendingPolicyResponse(BaseModel):
    id: int
    wallet_id: int
    required_approvals: int
    total_approvers: int
    block_sanctioned_addresses: bool


class AssignApproversResponse(BaseModel):
    wallet_id: int
    assigned_approvers: List[int]


class GrantReadonlyAccessResponse(BaseModel):
    wallet_id: int
    role_id: int
    access_level: str


app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/roles", response_model=List[RoleResponse], operation_id="find_roles")
def find_roles(name: str, db: Session = Depends(get_db)):
    roles = db.query(Role).filter(Role.name.like(f"%{name}%")).all()
    return roles


@app.post("/wallets", response_model=WalletResponse, operation_id="create_cold_wallet")
def create_cold_wallet(wallet_data: CreateColdWalletRequest, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    wallet = Wallet(
        label=wallet_data.label,
        wallet_type="cold",
        blockchain=wallet_data.blockchain,
        bip_standard=wallet_data.bip_standard,
        created_by=current_user_id,
    )
    db.add(wallet)
    db.commit()
    db.refresh(wallet)
    return wallet


@app.post("/spending_policies", response_model=SpendingPolicyResponse, operation_id="configure_spending_policy")
def configure_spending_policy(policy_data: ConfigureSpendingPolicyRequest, db: Session = Depends(get_db)):
    policy = SpendingPolicy(
        wallet_id=policy_data.wallet_id,
        required_approvals=policy_data.required_approvals,
        total_approvers=policy_data.total_approvers,
        block_sanctioned_addresses=1 if policy_data.block_sanctioned_addresses else 0,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@app.post("/wallet_approvers", response_model=AssignApproversResponse, operation_id="assign_approvers")
def assign_approvers(approver_data: AssignApproversRequest, db: Session = Depends(get_db)):
    # Clear existing approvers for this wallet
    db.query(WalletApprover).filter(WalletApprover.wallet_id == approver_data.wallet_id).delete()

    # Add new approvers
    for i, user_id in enumerate(approver_data.user_ids, 1):
        approver = WalletApprover(
            wallet_id=approver_data.wallet_id,
            user_id=user_id,
            approval_order=i,
        )
        db.add(approver)

    db.commit()

    return AssignApproversResponse(
        wallet_id=approver_data.wallet_id,
        assigned_approvers=approver_data.user_ids,
    )


@app.post("/wallet_access", response_model=GrantReadonlyAccessResponse, operation_id="grant_readonly_access")
def grant_readonly_access(access_data: GrantReadonlyAccessRequest, db: Session = Depends(get_db)):
    role = db.query(Role).filter(Role.name == access_data.role_name).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Ensure access_level is one of the allowed values
    if access_data.access_level not in ['read', 'write', 'admin']:
        raise HTTPException(status_code=400, detail="Invalid access level")

    access_record = WalletAccess(
        wallet_id=access_data.wallet_id,
        role_id=role.id,
        access_level=access_data.access_level,
        granted_by=current_user_id,
    )
    db.add(access_record)
    db.commit()
    db.refresh(access_record)

    return GrantReadonlyAccessResponse(
        wallet_id=access_data.wallet_id,
        role_id=role.id,
        access_level=access_data.access_level,
    )
