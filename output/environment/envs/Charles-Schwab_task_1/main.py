import os
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    Date,
    DateTime,
    Text,
    create_engine,
    select,
    insert,
    update,
    and_,
)
from sqlalchemy.orm import declarative_base, Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
Base = declarative_base()


# --- SQLAlchemy Models ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    first_name = Column(String)
    last_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    account_type = Column(Text, nullable=False)
    account_number = Column(String, unique=True, nullable=False)
    account_name = Column(Text, nullable=False)
    balance = Column(Float, default=0.0)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class LinkedAccount(Base):
    __tablename__ = "linked_accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    source_account_id = Column(Integer, nullable=False)
    target_account_id = Column(Integer, nullable=False)
    link_type = Column(Text, nullable=False)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class RecurringContribution(Base):
    __tablename__ = "recurring_contributions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    linked_account_id = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    frequency = Column(Text, nullable=False)
    day_of_month = Column(Integer)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date)
    next_run_date = Column(Date, nullable=False)
    status = Column(Text, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ContributionTransaction(Base):
    __tablename__ = "contribution_transactions"
    id = Column(Integer, primary_key=True)
    recurring_contribution_id = Column(Integer)
    user_id = Column(Integer, nullable=False)
    source_account_id = Column(Integer, nullable=False)
    target_account_id = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    transaction_date = Column(DateTime, default=datetime.utcnow)
    status = Column(Text, default="pending")
    failure_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


# --- Pydantic Schemas ---

class AccountResponse(BaseModel):
    id: int
    account_type: str
    account_number: str
    account_name: str
    balance: float

    class Config:
        from_attributes = True


class CreateLinkedAccountRequest(BaseModel):
    source_account_id: int
    target_account_id: int


class LinkedAccountResponse(BaseModel):
    id: int
    user_id: int
    source_account_id: int
    target_account_id: int
    link_type: str
    is_active: bool

    class Config:
        from_attributes = True


class CreateRecurringContributionRequest(BaseModel):
    linked_account_id: int
    amount: float


class RecurringContributionResponse(BaseModel):
    id: int
    user_id: int
    linked_account_id: int
    amount: float
    frequency: str
    day_of_month: int
    start_date: date
    next_run_date: date
    status: str

    class Config:
        from_attributes = True


# --- App Setup ---

app = FastAPI(title="Charles Schwab API", version="1.0.0")


def get_db():
    with Session(engine) as session:
        yield session


def get_current_user_id() -> int:
    try:
        return int(os.environ["AWM_CURRENT_USER_ID"])
    except KeyError:
        raise HTTPException(status_code=401, detail="User ID not provided")


@app.get("/accounts", operation_id="find_accounts")
def find_accounts(account_type: str = Query(...)):
    """Find user's accounts by type"""
    current_user_id = get_current_user_id()
    
    stmt = select(Account).where(
        and_(
            Account.user_id == current_user_id,
            Account.account_type == account_type,
            Account.is_active == 1
        )
    )
    
    with Session(engine) as db:
        results = db.execute(stmt).scalars().all()
        
    if not results:
        raise HTTPException(status_code=404, detail=f"No active {account_type} accounts found for current user")
        
    return [AccountResponse.model_validate(acc).model_dump() for acc in results]


@app.post("/linked-accounts", operation_id="create_linked_account")
def create_linked_account(req: CreateLinkedAccountRequest):
    """Link two accounts for transfers"""
    current_user_id = get_current_user_id()
    
    # Verify source and target accounts belong to the current user and are active
    stmt_check = select(Account).where(
        and_(
            Account.id.in_([req.source_account_id, req.target_account_id]),
            Account.user_id == current_user_id,
            Account.is_active == 1
        )
    )
    
    with Session(engine) as db:
        existing_accounts = db.execute(stmt_check).scalars().all()
        
        if len(existing_accounts) != 2:
            raise HTTPException(status_code=400, detail="Source or Target account not found or inactive for current user")
            
        # Check if linkage already exists
        stmt_link = select(LinkedAccount).where(
            and_(
                LinkedAccount.user_id == current_user_id,
                LinkedAccount.source_account_id == req.source_account_id,
                LinkedAccount.target_account_id == req.target_account_id,
                LinkedAccount.is_active == 1
            )
        )
        
        existing_link = db.execute(stmt_link).scalar_one_or_none()
        if existing_link:
            raise HTTPException(status_code=409, detail="Accounts are already linked")
            
        # Create new link
        new_link = LinkedAccount(
            user_id=current_user_id,
            source_account_id=req.source_account_id,
            target_account_id=req.target_account_id,
            link_type="internal",
            is_active=1
        )
        db.add(new_link)
        db.commit()
        db.refresh(new_link)
        
    return LinkedAccountResponse.model_validate(new_link).model_dump()


@app.post("/recurring-contributions", operation_id="create_recurring_contribution")
def create_recurring_contribution(req: CreateRecurringContributionRequest):
    """Create a new recurring contribution from a linked account to a Roth IRA"""
    current_user_id = get_current_user_id()
    
    # Validate linked account belongs to user and is active
    stmt_link = select(LinkedAccount).where(
        and_(
            LinkedAccount.id == req.linked_account_id,
            LinkedAccount.user_id == current_user_id,
            LinkedAccount.is_active == 1
        )
    )
    
    with Session(engine) as db:
        link = db.execute(stmt_link).scalar_one_or_none()
        
        if not link:
            raise HTTPException(status_code=404, detail="Linked account not found or inactive")
            
        # Calculate dates
        today = date.today()
        # Start on the 15th of next month
        if today.month == 12:
            next_month_year = today.year + 1
            next_month_day = 1
        else:
            next_month_year = today.year
            next_month_day = today.month + 1
            
        start_date = date(next_month_year, next_month_day, 15)
        
        # Ensure start_date is at least tomorrow if it's today or past
        if start_date <= today:
             # If we are past the 15th of next month logic implies next-next month? 
             # Spec says "starting on the 15th of next month". 
             # Let's stick to strict interpretation: Next calendar month's 15th.
             pass

        next_run = start_date
        
        new_contribution = RecurringContribution(
            user_id=current_user_id,
            linked_account_id=req.linked_account_id,
            amount=req.amount,
            frequency="monthly",
            day_of_month=15,
            start_date=start_date,
            end_date=None,
            next_run_date=next_run,
            status="active"
        )
        
        db.add(new_contribution)
        db.commit()
        db.refresh(new_contribution)
        
    return RecurringContributionResponse.model_validate(new_contribution).model_dump()
