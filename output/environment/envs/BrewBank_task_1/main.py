import os
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Ingredient(Base):
    __tablename__ = "ingredients"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    alpha_acid = Column(Float)
    purchase_date = Column(String)
    expiration_date = Column(String)
    cost = Column(Float)
    quantity = Column(Float, nullable=False)
    unit = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("category IN ('hops', 'malt', 'yeast', 'adjunct', 'other')"),
        CheckConstraint("alpha_acid IS NULL OR (alpha_acid >= 0 AND alpha_acid <= 100)"),
        CheckConstraint("cost IS NULL OR cost >= 0"),
        CheckConstraint("quantity > 0"),
        CheckConstraint("unit IN ('kg', 'g', 'lb', 'oz', 'L', 'mL', 'gal')"),
    )

app = FastAPI()

class IngredientCreate(BaseModel):
    name: str
    category: Literal['hops', 'malt', 'yeast', 'adjunct', 'other']
    alpha_acid: Optional[float] = None
    purchase_date: str
    expiration_date: str
    cost: float
    quantity: float
    unit: Literal['kg', 'g', 'lb', 'oz', 'L', 'mL', 'gal']

class IngredientResponse(BaseModel):
    id: int
    user_id: int
    name: str
    category: Literal['hops', 'malt', 'yeast', 'adjunct', 'other']
    alpha_acid: Optional[float] = None
    purchase_date: str
    expiration_date: str
    cost: float
    quantity: float
    unit: Literal['kg', 'g', 'lb', 'oz', 'L', 'mL', 'gal']
    created_at: str
    updated_at: str

@app.post("/ingredients", response_model=IngredientResponse, operation_id="add_ingredient")
def add_ingredient(ingredient: IngredientCreate):
    db = SessionLocal()
    try:
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
        if current_user_id == 0:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        db_ingredient = Ingredient(
            user_id=current_user_id,
            name=ingredient.name,
            category=ingredient.category,
            alpha_acid=ingredient.alpha_acid,
            purchase_date=ingredient.purchase_date,
            expiration_date=ingredient.expiration_date,
            cost=ingredient.cost,
            quantity=ingredient.quantity,
            unit=ingredient.unit
        )
        
        db.add(db_ingredient)
        db.commit()
        db.refresh(db_ingredient)
        
        # Convert datetime objects to strings for response
        return IngredientResponse(
            id=db_ingredient.id,
            user_id=db_ingredient.user_id,
            name=db_ingredient.name,
            category=db_ingredient.category,
            alpha_acid=db_ingredient.alpha_acid,
            purchase_date=db_ingredient.purchase_date,
            expiration_date=db_ingredient.expiration_date,
            cost=db_ingredient.cost,
            quantity=db_ingredient.quantity,
            unit=db_ingredient.unit,
            created_at=db_ingredient.created_at.isoformat() if db_ingredient.created_at else None,
            updated_at=db_ingredient.updated_at.isoformat() if db_ingredient.updated_at else None
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()
