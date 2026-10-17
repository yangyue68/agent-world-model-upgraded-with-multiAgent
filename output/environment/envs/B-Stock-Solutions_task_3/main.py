import os
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, ForeignKey, CheckConstraint, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    category = Column(String)
    location = Column(String)
    auction_start = Column(DateTime)
    auction_end = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class AutoBidRule(Base):
    __tablename__ = "auto_bid_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    lot_id = Column(String, ForeignKey("lots.id"), nullable=False)
    max_bid = Column(Float, nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("max_bid > 0", name="chk_max_bid_positive"),
        CheckConstraint("is_active IN (0,1)", name="chk_is_active_valid"),
    )

app = FastAPI()

class SetAutoBidRuleRequest(BaseModel):
    max_bid: float

class SetAutoBidRuleResponse(BaseModel):
    message: str
    rule_id: int

@app.post("/lots/{lot_id}/auto_bid_rule", response_model=SetAutoBidRuleResponse, operation_id="set_auto_bid_rule")
def set_auto_bid_rule(lot_id: str, request: SetAutoBidRuleRequest):
    db = SessionLocal()
    try:
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
        
        # Cancel existing active auto-bid rules for this lot
        db.execute(
            text("UPDATE auto_bid_rules SET is_active = 0 WHERE lot_id = :lot_id AND user_id = :user_id AND is_active = 1"),
            {"lot_id": lot_id, "user_id": current_user_id}
        )
        
        # Create new auto-bid rule
        new_rule = AutoBidRule(
            user_id=current_user_id,
            lot_id=lot_id,
            max_bid=request.max_bid,
            is_active=1
        )
        db.add(new_rule)
        db.flush()  # Get the ID without committing
        
        rule_id = new_rule.id
        db.commit()
        
        return SetAutoBidRuleResponse(
            message=f"Auto-bid rule set successfully for lot {lot_id}",
            rule_id=rule_id
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()
