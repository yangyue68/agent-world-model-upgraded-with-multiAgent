import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Tier(Base):
    __tablename__ = "tiers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    min_lifetime_points = Column(Integer, nullable=False)
    discount_percent = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Member(Base):
    __tablename__ = "members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    lifetime_points = Column(Integer, nullable=False, default=0)
    tier_id = Column(Integer, ForeignKey("tiers.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, unique=True)
    email = Column(String, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Purchase(Base):
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False)
    amount = Column(Float, nullable=False)
    discount_percent = Column(Float, nullable=False, default=0.0)
    final_amount = Column(Float, nullable=False)
    points_earned = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class TierProgressionRule(Base):
    __tablename__ = "tier_progression_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_tier_id = Column(Integer, ForeignKey("tiers.id"), nullable=True)
    to_tier_id = Column(Integer, ForeignKey("tiers.id"), nullable=False)
    threshold_points = Column(Integer, nullable=False)
    discount_percent = Column(Float, nullable=False, default=0.0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# Pydantic Schemas

class TierProgressionRuleCreate(BaseModel):
    from_tier_id: Optional[int] = None
    to_tier_id: int
    threshold_points: int
    discount_percent: float
    is_active: bool = True


class TierProgressionRuleUpdate(BaseModel):
    from_tier_id: Optional[int] = None
    to_tier_id: Optional[int] = None
    threshold_points: Optional[int] = None
    discount_percent: Optional[float] = None
    is_active: Optional[bool] = None


class TierProgressionRuleResponse(BaseModel):
    id: int
    from_tier_id: Optional[int]
    to_tier_id: int
    threshold_points: int
    discount_percent: float
    is_active: bool
    created_at: str
    updated_at: str

    class Config:
        orm_mode = True


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI(title="ClubStack Loyalty API")


@app.post("/tier-progression-rules", response_model=TierProgressionRuleResponse, operation_id="create_tier_progression_rule")
def create_tier_progression_rule(rule_data: TierProgressionRuleCreate, db: Session = Depends(get_db)):
    # Validate that tiers exist if provided
    if rule_data.from_tier_id is not None:
        from_tier = db.query(Tier).filter(Tier.id == rule_data.from_tier_id).first()
        if not from_tier:
            raise HTTPException(status_code=404, detail=f"From tier with ID {rule_data.from_tier_id} not found.")
    
    to_tier = db.query(Tier).filter(Tier.id == rule_data.to_tier_id).first()
    if not to_tier:
        raise HTTPException(status_code=404, detail=f"To tier with ID {rule_data.to_tier_id} not found.")

    new_rule = TierProgressionRule(
        from_tier_id=rule_data.from_tier_id,
        to_tier_id=rule_data.to_tier_id,
        threshold_points=rule_data.threshold_points,
        discount_percent=rule_data.discount_percent,
        is_active=rule_data.is_active,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)
    
    return _serialize_rule(new_rule)


@app.get("/tier-progression-rules", response_model=List[TierProgressionRuleResponse], operation_id="find_tier_progression_rules")
def find_tier_progression_rules(
    is_active: Optional[bool] = Query(None, description="Filter rules by whether they are currently active."),
    threshold_points: Optional[int] = Query(None, description="Filter rules that apply at or above this point threshold."),
    db: Session = Depends(get_db)
):
    query = db.query(TierProgressionRule)
    
    if is_active is not None:
        query = query.filter(TierProgressionRule.is_active == is_active)
        
    if threshold_points is not None:
        query = query.filter(TierProgressionRule.threshold_points >= threshold_points)
        
    rules = query.all()
    return [_serialize_rule(r) for r in rules]


@app.put("/tier-progression-rules/{id}", response_model=TierProgressionRuleResponse, operation_id="update_tier_progression_rule")
def update_tier_progression_rule(id: int, rule_data: TierProgressionRuleUpdate, db: Session = Depends(get_db)):
    rule = db.query(TierProgressionRule).filter(TierProgressionRule.id == id).first()
    if not rule:
        raise HTTPException(status_code=404, detail=f"Tier progression rule with ID {id} not found.")

    if rule_data.from_tier_id is not None:
        from_tier = db.query(Tier).filter(Tier.id == rule_data.from_tier_id).first()
        if not from_tier:
            raise HTTPException(status_code=404, detail=f"From tier with ID {rule_data.from_tier_id} not found.")
        rule.from_tier_id = rule_data.from_tier_id
        
    if rule_data.to_tier_id is not None:
        to_tier = db.query(Tier).filter(Tier.id == rule_data.to_tier_id).first()
        if not to_tier:
            raise HTTPException(status_code=404, detail=f"To tier with ID {rule_data.to_tier_id} not found.")
        rule.to_tier_id = rule_data.to_tier_id
        
    if rule_data.threshold_points is not None:
        rule.threshold_points = rule_data.threshold_points
        
    if rule_data.discount_percent is not None:
        rule.discount_percent = rule_data.discount_percent
        
    if rule_data.is_active is not None:
        rule.is_active = rule_data.is_active
        
    rule.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rule)
    
    return _serialize_rule(rule)


@app.delete("/tier-progression-rules/{id}", response_model=dict, operation_id="delete_tier_progression_rule")
def delete_tier_progression_rule(id: int, db: Session = Depends(get_db)):
    rule = db.query(TierProgressionRule).filter(TierProgressionRule.id == id).first()
    if not rule:
        raise HTTPException(status_code=404, detail=f"Tier progression rule with ID {id} not found.")
        
    db.delete(rule)
    db.commit()
    
    return {"message": f"Tier progression rule with ID {id} deleted successfully."}


def _serialize_rule(rule: TierProgressionRule) -> dict:
    return {
        "id": rule.id,
        "from_tier_id": rule.from_tier_id,
        "to_tier_id": rule.to_tier_id,
        "threshold_points": rule.threshold_points,
        "discount_percent": rule.discount_percent,
        "is_active": rule.is_active,
        "created_at": rule.created_at.isoformat(),
        "updated_at": rule.updated_at.isoformat()
    }
