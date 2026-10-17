import os
from datetime import datetime
from typing import Literal
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Depends
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
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
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    wallet_id = Column(String, unique=True, nullable=False)
    label = Column(String)
    owner_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Transfer(Base):
    __tablename__ = "transfers"
    id = Column(Integer, primary_key=True, index=True)
    source_wallet_id = Column(Integer, ForeignKey("wallets.id"))
    destination_address = Column(String, nullable=False)
    amount_usd = Column(Float, nullable=False)
    status = Column(String, default="pending")
    initiated_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Add check constraint for amount_usd
    __table_args__ = (
        CheckConstraint("amount_usd > 0", name="check_amount_usd_positive"),
        CheckConstraint("status IN ('pending','screening','approved','rejected','completed','failed')", name="check_transfer_status"),
    )

class ScreeningRequest(Base):
    __tablename__ = "screening_requests"
    id = Column(Integer, primary_key=True, index=True)
    transfer_id = Column(Integer, ForeignKey("transfers.id"))
    address = Column(String, nullable=False)
    screening_type = Column(String, nullable=False)
    status = Column(String, default="pending")
    requested_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Add check constraints
    __table_args__ = (
        CheckConstraint("screening_type IN ('ofac','global_sanctions','ofac_and_global_sanctions')", name="check_screening_type"),
        CheckConstraint("status IN ('pending','in_progress','completed','failed')", name="check_screening_status"),
    )

class ScreeningResult(Base):
    __tablename__ = "screening_results"
    id = Column(Integer, primary_key=True, index=True)
    screening_request_id = Column(Integer, ForeignKey("screening_requests.id"), nullable=False)
    result = Column(String, nullable=False)
    details = Column(String)
    screened_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Add check constraint
    __table_args__ = (
        CheckConstraint("result IN ('clear','hit','error')", name="check_screening_result"),
    )

class SanctionsList(Base):
    __tablename__ = "sanctions_lists"
    id = Column(Integer, primary_key=True, index=True)
    list_name = Column(String, unique=True, nullable=False)
    description = Column(String)
    last_updated = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SanctionedAddress(Base):
    __tablename__ = "sanctioned_addresses"
    id = Column(Integer, primary_key=True, index=True)
    address = Column(String, nullable=False)
    sanctions_list_id = Column(Integer, ForeignKey("sanctions_lists.id"))
    entity_name = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer)
    details = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

# Request Models
class TransferRequest(BaseModel):
    source_wallet_id: str
    destination_address: str
    amount_usd: float

class ScreeningRequestRequest(BaseModel):
    transfer_id: int
    address: str
    screening_type: Literal["ofac", "global_sanctions", "ofac_and_global_sanctions"]

# Response Models
class TransferResponse(BaseModel):
    id: int
    source_wallet_id: str
    destination_address: str
    amount_usd: float
    status: str
    initiated_by_user_id: int
    created_at: str
    updated_at: str

class ScreeningRequestResponse(BaseModel):
    id: int
    transfer_id: int
    address: str
    screening_type: str
    status: str
    requested_by_user_id: int
    created_at: str
    updated_at: str

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/transfers", response_model=TransferResponse, operation_id="initiate_transfer")
def initiate_transfer(transfer: TransferRequest, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Validate wallet exists
    wallet = db.query(Wallet).filter(Wallet.wallet_id == transfer.source_wallet_id).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    
    # Validate user owns wallet
    if wallet.owner_user_id != current_user_id:
        raise HTTPException(status_code=403, detail="User does not own this wallet")
    
    # Create transfer
    db_transfer = Transfer(
        source_wallet_id=wallet.id,
        destination_address=transfer.destination_address,
        amount_usd=transfer.amount_usd,
        initiated_by_user_id=current_user_id
    )
    db.add(db_transfer)
    db.commit()
    db.refresh(db_transfer)
    
    # Create audit log
    audit_log = AuditLog(
        user_id=current_user_id,
        action="create_transfer",
        entity_type="transfers",
        entity_id=db_transfer.id,
        details=f"Initiated transfer of ${transfer.amount_usd} to {transfer.destination_address}"
    )
    db.add(audit_log)
    db.commit()
    
    # Trigger screening request
    screening_request = ScreeningRequest(
        transfer_id=db_transfer.id,
        address=transfer.destination_address,
        screening_type="ofac_and_global_sanctions",
        requested_by_user_id=current_user_id
    )
    db.add(screening_request)
    db.commit()
    db.refresh(screening_request)
    
    # Create audit log for screening request
    audit_log2 = AuditLog(
        user_id=current_user_id,
        action="request_screening",
        entity_type="screening_requests",
        entity_id=screening_request.id,
        details=f"Requested OFAC and global sanctions screening for address {transfer.destination_address}"
    )
    db.add(audit_log2)
    db.commit()
    
    return TransferResponse(
        id=db_transfer.id,
        source_wallet_id=transfer.source_wallet_id,
        destination_address=transfer.destination_address,
        amount_usd=transfer.amount_usd,
        status=db_transfer.status,
        initiated_by_user_id=current_user_id,
        created_at=db_transfer.created_at.isoformat(),
        updated_at=db_transfer.updated_at.isoformat()
    )

@app.post("/screening_requests", response_model=ScreeningRequestResponse, operation_id="request_screening")
def request_screening(screening: ScreeningRequestRequest, db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Validate transfer exists
    transfer = db.query(Transfer).filter(Transfer.id == screening.transfer_id).first()
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    
    # Validate user initiated the transfer
    if transfer.initiated_by_user_id != current_user_id:
        raise HTTPException(status_code=403, detail="User did not initiate this transfer")
    
    # Create screening request
    db_screening = ScreeningRequest(
        transfer_id=screening.transfer_id,
        address=screening.address,
        screening_type=screening.screening_type,
        requested_by_user_id=current_user_id
    )
    db.add(db_screening)
    db.commit()
    db.refresh(db_screening)
    
    # Update transfer status to screening
    transfer.status = "screening"
    db.commit()
    
    # Create audit log
    audit_log = AuditLog(
        user_id=current_user_id,
        action="request_screening",
        entity_type="screening_requests",
        entity_id=db_screening.id,
        details=f"Requested {screening.screening_type} screening for address {screening.address}"
    )
    db.add(audit_log)
    db.commit()
    
    return ScreeningRequestResponse(
        id=db_screening.id,
        transfer_id=db_screening.transfer_id,
        address=db_screening.address,
        screening_type=db_screening.screening_type,
        status=db_screening.status,
        requested_by_user_id=current_user_id,
        created_at=db_screening.created_at.isoformat(),
        updated_at=db_screening.updated_at.isoformat()
    )
