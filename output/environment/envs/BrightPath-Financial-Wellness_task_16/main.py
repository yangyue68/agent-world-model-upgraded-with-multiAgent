from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, Text, select, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime, date

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
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    balance = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    parent_id = Column(Integer, ForeignKey("categories.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"))
    amount = Column(Float, nullable=False)
    description = Column(Text)
    transaction_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class TransactionTag(Base):
    __tablename__ = "transaction_tags"
    transaction_id = Column(Integer, ForeignKey("transactions.id"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tags.id"), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class TransactionResponse(BaseModel):
    id: int
    description: str
    amount: float
    transaction_date: date

class VacationTransactionsResponse(BaseModel):
    transactions: list[TransactionResponse]
    total_amount: float

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/reports/vacation-transactions", response_model=VacationTransactionsResponse, operation_id="get_vacation_transactions")
def get_vacation_transactions(db = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Get the vacation tag ID for the current user
    vacation_tag_query = select(Tag.id).where(
        (Tag.user_id == current_user_id) & 
        (Tag.name == "Vacation")
    )
    vacation_tag_result = db.execute(vacation_tag_query).fetchone()
    
    if not vacation_tag_result:
        raise HTTPException(status_code=404, detail="No 'Vacation' tag found for current user")
    
    vacation_tag_id = vacation_tag_result[0]
    
    # Get all transactions with the vacation tag for the current user
    stmt = select(
        Transaction.id,
        Transaction.description,
        Transaction.amount,
        Transaction.transaction_date
    ).select_from(Transaction)\
    .join(TransactionTag, Transaction.id == TransactionTag.transaction_id)\
    .join(Tag, TransactionTag.tag_id == Tag.id)\
    .where(
        (Transaction.user_id == current_user_id) &
        (Tag.id == vacation_tag_id)
    )\
    .order_by(Transaction.transaction_date)
    
    results = db.execute(stmt).fetchall()
    
    if not results:
        return VacationTransactionsResponse(transactions=[], total_amount=0.0)
    
    transactions = [
        TransactionResponse(
            id=row[0],
            description=row[1],
            amount=row[2],
            transaction_date=row[3]
        ) for row in results
    ]
    
    total_amount = sum(t.amount for t in transactions)
    
    return VacationTransactionsResponse(
        transactions=transactions,
        total_amount=total_amount
    )
