import os
from datetime import date
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, DateTime, Text, ForeignKey, func, case
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
    username = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=False, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())

class Account(Base):
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    industry = Column(String)
    website = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())

class Contact(Base):
    __tablename__ = "contacts"
    
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    
    account = relationship("Account")

class Opportunity(Base):
    __tablename__ = "opportunities"
    
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    name = Column(String, nullable=False)
    stage = Column(String, nullable=False, default="Prospecting")
    amount = Column(Float)
    close_date = Column(Date)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    
    account = relationship("Account")

class Quote(Base):
    __tablename__ = "quotes"
    
    id = Column(Integer, primary_key=True, index=True)
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"), nullable=False)
    quote_number = Column(String, nullable=False, unique=True)
    status = Column(String, nullable=False, default="Draft")
    valid_until = Column(Date, nullable=False)
    total_amount = Column(Float, nullable=False, default=0.0)
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    
    opportunity = relationship("Opportunity")

class QuoteLineItem(Base):
    __tablename__ = "quote_line_items"
    
    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id"), nullable=False)
    product_name = Column(String, nullable=False)
    description = Column(Text)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Float, nullable=False, default=0.0)
    line_total = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, default=func.current_timestamp())
    
    quote = relationship("Quote")

# Pydantic Models
class QuoteResponse(BaseModel):
    id: int
    quote_number: str
    valid_until: date
    status: str
    total_amount: float

class QuoteVoidResponse(BaseModel):
    id: int
    quote_number: str
    status: str
    updated_at: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/quotes/expired", response_model=List[QuoteResponse], operation_id="find_expired_quotes")
def find_expired_quotes(db = Depends(get_db)):
    today = date.today()
    expired_quotes = db.query(Quote).filter(Quote.valid_until < today).all()
    return expired_quotes

@app.put("/quotes/{id}/void", response_model=QuoteVoidResponse, operation_id="void_quote")
def void_quote(id: int, db = Depends(get_db)):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    quote = db.query(Quote).filter(Quote.id == id).first()
    
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    
    # Update status to Voided
    quote.status = "Voided"
    db.commit()
    db.refresh(quote)
    
    return {
        "id": quote.id,
        "quote_number": quote.quote_number,
        "status": quote.status,
        "updated_at": quote.updated_at.isoformat() if quote.updated_at else None
    }
