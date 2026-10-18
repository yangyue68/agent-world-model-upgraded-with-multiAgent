import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    Float,
    Text,
    DateTime,
    ForeignKey,
    create_engine,
    select,
    case,
)
from sqlalchemy.orm import Session, declarative_base, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
Base = declarative_base()


# --- SQLAlchemy Models ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(Text, unique=True, nullable=False)
    full_name = Column(Text)
    email = Column(Text, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    account_type = Column(Text, nullable=False)
    account_number_last4 = Column(Text, nullable=False)
    account_name = Column(Text, nullable=False)
    current_balance = Column(Float, nullable=False, default=0.0)
    available_balance = Column(Float, nullable=False, default=0.0)
    currency = Column(Text, nullable=False, default="USD")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    transactions = relationship("Transaction", back_populates="account")


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    amount = Column(Float, nullable=False)
    transaction_type = Column(Text, nullable=False)
    description = Column(Text)
    transaction_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    balance_after = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    account = relationship("Account", back_populates="transactions")


# --- Pydantic Schemas ---

class AccountSummaryResponse(BaseModel):
    class AccountDetail(BaseModel):
        id: int
        account_type: str
        account_number_last4: str
        account_name: str
        current_balance: float
        available_balance: float
        currency: str

    class TransactionDetail(BaseModel):
        id: int
        amount: float
        transaction_type: str
        description: str
        transaction_date: str  # ISO-8601 string
        balance_after: float

    account: AccountDetail
    transactions: List[TransactionDetail]


# --- FastAPI App ---

app = FastAPI(title="Chase API", version="1.0.0")


@app.get(
    "/primary-resource/account-summary",
    operation_id="get_account_summary",
    summary="Get account summary for Chase Total Checking account ending in 4321",
    response_model=AccountSummaryResponse,
    tags=["PrimaryResource"],
)
def get_account_summary(account_number_last4: str = Query(...)):
    """
    Retrieve the current available balance and last 5 transactions for the specified Chase Total Checking account.
    Requires authentication as the account owner.
    """
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with Session(engine) as session:
        # Find the account owned by the current user with the matching last 4 digits
        stmt = select(Account).where(
            Account.user_id == current_user_id,
            Account.account_number_last4 == account_number_last4
        )
        account = session.execute(stmt).scalar_one_or_none()

        if not account:
            raise HTTPException(status_code=404, detail="Account not found or access denied")

        # Get the last 5 transactions for this account, ordered by date descending
        tx_stmt = select(Transaction).where(
            Transaction.account_id == account.id
        ).order_by(
            Transaction.transaction_date.desc()
        ).limit(5)
        
        transactions = session.execute(tx_stmt).scalars().all()

        # Format the response
        account_data = {
            "id": account.id,
            "account_type": account.account_type,
            "account_number_last4": account.account_number_last4,
            "account_name": account.account_name,
            "current_balance": account.current_balance,
            "available_balance": account.available_balance,
            "currency": account.currency,
        }

        transactions_data = []
        for tx in transactions:
            # Ensure transaction_date is formatted correctly for JSON
            tx_date_str = tx.transaction_date.isoformat() if isinstance(tx.transaction_date, datetime) else str(tx.transaction_date)
            
            transactions_data.append({
                "id": tx.id,
                "amount": tx.amount,
                "transaction_type": tx.transaction_type,
                "description": tx.description,
                "transaction_date": tx_date_str,
                "balance_after": tx.balance_after,
            })

        return AccountSummaryResponse(
            account=AccountSummaryResponse.AccountDetail(**account_data),
            transactions=[AccountSummaryResponse.TransactionDetail(**t) for t in transactions_data]
        )
