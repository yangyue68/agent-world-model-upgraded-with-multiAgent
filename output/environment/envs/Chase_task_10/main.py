import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class AccountResponse(BaseModel):
    id: int
    user_id: int
    account_type: str
    account_number_last4: str
    account_name: Optional[str] = None
    balance: float
    created_at: str
    updated_at: str


class PayeeResponse(BaseModel):
    id: int
    user_id: int
    name: str
    nickname: Optional[str] = None
    account_number: Optional[str] = None
    created_at: str
    updated_at: str


class PaymentRequest(BaseModel):
    account_id: int
    payee_id: int
    amount: float
    delivery_date: str
    memo: Optional[str] = None


class PaymentResponse(BaseModel):
    id: int
    user_id: int
    account_id: int
    payee_id: int
    amount: float
    delivery_date: str
    status: str
    memo: Optional[str] = None
    created_at: str
    updated_at: str


def get_current_user_id():
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")


@app.get("/accounts", response_model=List[AccountResponse], operation_id="find_accounts")
def find_accounts(account_type: Optional[str] = Query(None)):
    user_id = get_current_user_id()
    with SessionLocal() as db:
        query = text("""
            SELECT id, user_id, account_type, account_number_last4, account_name, 
                   balance, created_at, updated_at
            FROM accounts
            WHERE user_id = :user_id
        """)
        params = {"user_id": user_id}
        if account_type:
            query = text("""
                SELECT id, user_id, account_type, account_number_last4, account_name, 
                       balance, created_at, updated_at
                FROM accounts
                WHERE user_id = :user_id AND account_type = :account_type
            """)
            params["account_type"] = account_type
        
        results = db.execute(query, params).fetchall()
        
        accounts = []
        for row in results:
            accounts.append(AccountResponse(
                id=row.id,
                user_id=row.user_id,
                account_type=row.account_type,
                account_number_last4=row.account_number_last4,
                account_name=row.account_name,
                balance=float(row.balance),
                created_at=str(row.created_at),
                updated_at=str(row.updated_at)
            ))
        return accounts


@app.get("/payees", response_model=List[PayeeResponse], operation_id="find_payees")
def find_payees():
    user_id = get_current_user_id()
    with SessionLocal() as db:
        query = text("""
            SELECT id, user_id, name, nickname, account_number, created_at, updated_at
            FROM payees
            WHERE user_id = :user_id
        """)
        results = db.execute(query, {"user_id": user_id}).fetchall()
        
        payees = []
        for row in results:
            payees.append(PayeeResponse(
                id=row.id,
                user_id=row.user_id,
                name=row.name,
                nickname=row.nickname,
                account_number=row.account_number,
                created_at=str(row.created_at),
                updated_at=str(row.updated_at)
            ))
        return payees


@app.post("/payments", response_model=PaymentResponse, operation_id="schedule_payment")
def schedule_payment(payment_req: PaymentRequest):
    user_id = get_current_user_id()
    
    # Validate amount > 0
    if payment_req.amount <= 0:
        raise HTTPException(status_code=422, detail="Amount must be greater than 0")
    
    # Validate delivery date format
    try:
        datetime.strptime(payment_req.delivery_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid delivery date format. Expected YYYY-MM-DD")
    
    with SessionLocal() as db:
        # Verify account exists and belongs to user
        account_query = text("""
            SELECT id, user_id, account_type FROM accounts WHERE id = :account_id
        """)
        account_result = db.execute(account_query, {"account_id": payment_req.account_id}).fetchone()
        if not account_result:
            raise HTTPException(status_code=404, detail="Account not found")
        if account_result.user_id != user_id:
            raise HTTPException(status_code=403, detail="Account does not belong to current user")
        
        # Verify payee exists and belongs to user
        payee_query = text("""
            SELECT id, user_id FROM payees WHERE id = :payee_id
        """)
        payee_result = db.execute(payee_query, {"payee_id": payment_req.payee_id}).fetchone()
        if not payee_result:
            raise HTTPException(status_code=404, detail="Payee not found")
        if payee_result.user_id != user_id:
            raise HTTPException(status_code=403, detail="Payee does not belong to current user")
        
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_query = text("""
            INSERT INTO payments (user_id, account_id, payee_id, amount, delivery_date, status, memo, created_at, updated_at)
            VALUES (:user_id, :account_id, :payee_id, :amount, :delivery_date, 'scheduled', :memo, :created_at, :updated_at)
        """)
        
        result = db.execute(insert_query, {
            "user_id": user_id,
            "account_id": payment_req.account_id,
            "payee_id": payment_req.payee_id,
            "amount": payment_req.amount,
            "delivery_date": payment_req.delivery_date,
            "memo": payment_req.memo,
            "created_at": now,
            "updated_at": now
        })
        db.commit()
        
        new_id = result.lastrowid
        
        select_query = text("""
            SELECT id, user_id, account_id, payee_id, amount, delivery_date, status, memo, created_at, updated_at
            FROM payments WHERE id = :id
        """)
        new_payment = db.execute(select_query, {"id": new_id}).fetchone()
        
        return PaymentResponse(
            id=new_payment.id,
            user_id=new_payment.user_id,
            account_id=new_payment.account_id,
            payee_id=new_payment.payee_id,
            amount=float(new_payment.amount),
            delivery_date=str(new_payment.delivery_date),
            status=new_payment.status,
            memo=new_payment.memo,
            created_at=str(new_payment.created_at),
            updated_at=str(new_payment.updated_at)
        )
