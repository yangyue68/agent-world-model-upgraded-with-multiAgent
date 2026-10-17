import os
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, status, Depends
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    wallet_type = Column(String, nullable=False)
    balance_btc = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Approver(Base):
    __tablename__ = "approvers"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    wallet_id = Column(Integer, ForeignKey("wallets.id"))
    role = Column(String, default="approver")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Transfer(Base):
    __tablename__ = "transfers"
    id = Column(Integer, primary_key=True, index=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"))
    destination_address = Column(String, nullable=False)
    amount_btc = Column(Float, nullable=False)
    memo = Column(String)
    status = Column(String, default="pending_approval")
    required_approvals = Column(Integer, default=1)
    time_lock_hours = Column(Integer, default=0)
    time_lock_until = Column(DateTime)
    initiated_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class TransferApproval(Base):
    __tablename__ = "transfer_approvals"
    id = Column(Integer, primary_key=True, index=True)
    transfer_id = Column(Integer, ForeignKey("transfers.id"))
    approver_id = Column(Integer, ForeignKey("approvers.id"))
    approved_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class TransferRequest(BaseModel):
    wallet_name: str
    destination_address: str
    amount_btc: float
    memo: Optional[str] = None
    required_approvals: int
    time_lock_hours: int

class TransferResponse(BaseModel):
    id: int
    wallet_id: int
    destination_address: str
    amount_btc: float
    memo: Optional[str] = None
    status: str
    required_approvals: int
    time_lock_hours: int
    time_lock_until: Optional[datetime] = None
    initiated_by: int
    created_at: datetime

class ApprovalResponse(BaseModel):
    id: int
    transfer_id: int
    approver_id: int
    approved_at: datetime
    user_full_name: str
    user_email: str

class ApprovalRequest(BaseModel):
    transfer_id: int

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/transfers", response_model=TransferResponse, operation_id="initiate_transfer")
def initiate_transfer(transfer_request: TransferRequest, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Get wallet
    wallet = db.query(Wallet).filter(Wallet.name == transfer_request.wallet_name).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    
    # Check if user is an approver for this wallet
    approver = db.query(Approver).filter(
        Approver.user_id == current_user_id,
        Approver.wallet_id == wallet.id
    ).first()
    if not approver:
        raise HTTPException(status_code=403, detail="User is not an approver for this wallet")
    
    # Validate amount
    if transfer_request.amount_btc <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    
    # Validate required approvals
    if transfer_request.required_approvals < 2:
        raise HTTPException(status_code=400, detail="Required approvals must be at least 2")
    
    # Validate time lock
    if transfer_request.time_lock_hours < 24:
        raise HTTPException(status_code=400, detail="Time lock must be at least 24 hours")
    
    # Create transfer
    new_transfer = Transfer(
        wallet_id=wallet.id,
        destination_address=transfer_request.destination_address,
        amount_btc=transfer_request.amount_btc,
        memo=transfer_request.memo,
        required_approvals=transfer_request.required_approvals,
        time_lock_hours=transfer_request.time_lock_hours,
        initiated_by=current_user_id
    )
    
    db.add(new_transfer)
    db.commit()
    db.refresh(new_transfer)
    
    # Set time lock until
    if new_transfer.time_lock_hours > 0:
        new_transfer.time_lock_until = datetime.utcnow() + timedelta(hours=new_transfer.time_lock_hours)
        new_transfer.status = "timelocked"
        db.commit()
        db.refresh(new_transfer)
    
    return new_transfer

@app.get("/transfers/{transfer_id}/approvals", response_model=List[ApprovalResponse], operation_id="get_transfer_approvals")
def get_transfer_approvals(transfer_id: int, db = Depends(get_db)):
    approvals = db.query(TransferApproval).filter(TransferApproval.transfer_id == transfer_id).all()
    
    result = []
    for approval in approvals:
        approver = db.query(Approver).filter(Approver.id == approval.approver_id).first()
        user = db.query(User).filter(User.id == approver.user_id).first()
        
        result.append(ApprovalResponse(
            id=approval.id,
            transfer_id=approval.transfer_id,
            approver_id=approval.approver_id,
            approved_at=approval.approved_at,
            user_full_name=user.full_name,
            user_email=user.email
        ))
    
    return result

@app.post("/transfers/{transfer_id}/approve", response_model=TransferResponse, operation_id="approve_transfer")
def approve_transfer(transfer_id: int, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Get transfer
    transfer = db.query(Transfer).filter(Transfer.id == transfer_id).first()
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    
    # Check if transfer is pending approval or timelocked
    if transfer.status not in ["pending_approval", "timelocked"]:
        raise HTTPException(status_code=400, detail="Transfer cannot be approved")
    
    # Check if user is an approver for this wallet
    approver = db.query(Approver).filter(
        Approver.user_id == current_user_id,
        Approver.wallet_id == transfer.wallet_id
    ).first()
    if not approver:
        raise HTTPException(status_code=403, detail="User is not an approver for this wallet")
    
    # Check if user has already approved
    existing_approval = db.query(TransferApproval).filter(
        TransferApproval.transfer_id == transfer_id,
        TransferApproval.approver_id == approver.id
    ).first()
    if existing_approval:
        raise HTTPException(status_code=400, detail="User has already approved this transfer")
    
    # Create approval
    new_approval = TransferApproval(
        transfer_id=transfer_id,
        approver_id=approver.id
    )
    db.add(new_approval)
    db.commit()
    db.refresh(new_approval)
    
    # Count approvals
    total_approvals = db.query(TransferApproval).filter(TransferApproval.transfer_id == transfer_id).count()
    
    # If we have enough approvals, update status
    if total_approvals >= transfer.required_approvals:
        transfer.status = "approved"
        db.commit()
        db.refresh(transfer)
    
    return transfer
