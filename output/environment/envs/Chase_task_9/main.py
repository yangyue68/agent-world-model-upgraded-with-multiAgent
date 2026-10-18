import os
from datetime import date, datetime
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    create_engine,
    select,
    update,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# --- SQLAlchemy Models for Reference/Mapping ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)  # Alias for compatibility if needed, though spec uses id
    username = Column(String, unique=True)
    email = Column(String, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    account_type = Column(String)
    account_number_last4 = Column(String)
    display_name = Column(String)
    balance = Column(Float)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class ScheduledTransfer(Base):
    __tablename__ = "scheduled_transfers"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    from_account_id = Column(Integer)
    to_account_id = Column(Integer)
    amount = Column(Float)
    scheduled_date = Column(Date)
    status = Column(String)
    memo = Column(String)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


# --- Pydantic Response Models ---

class AccountResponse(BaseModel):
    id: int
    user_id: int
    account_type: str
    account_number_last4: str
    display_name: str
    balance: float

    class Config:
        from_attributes = True


class ScheduledTransferResponse(BaseModel):
    id: int
    user_id: int
    from_account_id: int
    to_account_id: int
    amount: float
    scheduled_date: date  # Changed from str to date to match SQLAlchemy Date column type and avoid validation error
    status: str
    memo: str

    class Config:
        from_attributes = True


class CancelTransferResponse(BaseModel):
    id: int
    status: str

    class Config:
        from_attributes = True


app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/accounts", operation_id="find_accounts", tags=["PrimaryResource"])
def find_accounts(
    account_type: Optional[str] = Query(None, description="Type of account"),
    account_number_last4: Optional[str] = Query(None, description="Last 4 digits"),
):
    """Find user's accounts by type and last 4 digits"""
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])
    db = SessionLocal()
    try:
        stmt = select(Account).where(Account.user_id == current_user_id)
        
        if account_type:
            stmt = stmt.where(Account.account_type == account_type)
        if account_number_last4:
            stmt = stmt.where(Account.account_number_last4 == account_number_last4)
            
        results = db.execute(stmt).scalars().all()
        return [AccountResponse.model_validate(r) for r in results]
    finally:
        db.close()


@app.get("/scheduled_transfers", operation_id="find_scheduled_transfers", tags=["PrimaryResource"])
def find_scheduled_transfers(
    from_account_id: Optional[int] = Query(None, description="ID of the source account"),
    to_account_id: Optional[int] = Query(None, description="ID of the destination account"),
):
    """Find pending scheduled transfers for the current user"""
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])
    db = SessionLocal()
    try:
        stmt = select(ScheduledTransfer).where(ScheduledTransfer.user_id == current_user_id)
        
        if from_account_id is not None:
            stmt = stmt.where(ScheduledTransfer.from_account_id == from_account_id)
        if to_account_id is not None:
            stmt = stmt.where(ScheduledTransfer.to_account_id == to_account_id)
            
        results = db.execute(stmt).scalars().all()
        return [ScheduledTransferResponse.model_validate(r) for r in results]
    finally:
        db.close()


@app.put("/scheduled_transfers/{id}", operation_id="cancel_scheduled_transfer", tags=["PrimaryResource"])
def cancel_scheduled_transfer(id: int = Path(..., description="ID of the scheduled transfer to cancel")):
    """Cancel a pending scheduled transfer"""
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])
    db = SessionLocal()
    try:
        # Fetch the transfer to ensure it belongs to the user and is pending
        stmt = select(ScheduledTransfer).where(
            ScheduledTransfer.id == id,
            ScheduledTransfer.user_id == current_user_id
        )
        result = db.execute(stmt).scalar_one_or_none()
        
        if result is None:
            raise HTTPException(status_code=404, detail="Scheduled transfer not found or does not belong to current user")
        
        if result.status != 'pending':
            raise HTTPException(status_code=400, detail=f"Cannot cancel transfer with status '{result.status}'. Only pending transfers can be cancelled.")
        
        # Update status to cancelled
        update_stmt = (
            update(ScheduledTransfer)
            .where(ScheduledTransfer.id == id)
            .values(status='cancelled')
        )
        db.execute(update_stmt)
        db.commit()
        
        # Refresh to get updated state
        result.status = 'cancelled'
        
        return CancelTransferResponse(id=result.id, status=result.status)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
