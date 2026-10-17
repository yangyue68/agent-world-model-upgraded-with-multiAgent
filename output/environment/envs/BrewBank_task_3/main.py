import os
from datetime import datetime, timedelta
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define models based on the schema
class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Ingredient(Base):
    __tablename__ = 'ingredients'
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    category = Column(String)
    default_unit = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Inventory(Base):
    __tablename__ = 'inventory'
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    ingredient_id = Column(Integer, ForeignKey('ingredients.id'))
    quantity = Column(Float, nullable=False)
    unit = Column(String, nullable=False)
    expiration_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models for response
class ExpiringIngredient(BaseModel):
    ingredient_id: int
    ingredient_name: str
    category: str
    quantity: float
    unit: str
    expiration_date: str

    class Config:
        orm_mode = True

# Create the app
app = FastAPI()

@app.get("/inventory/expiring_soon", response_model=List[ExpiringIngredient], operation_id="find_expiring_ingredients")
def find_expiring_ingredients():
    db = SessionLocal()
    try:
        # Get the current user ID from environment
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
        
        # Calculate the date 30 days from now
        today = datetime.now().date()
        thirty_days_later = today + timedelta(days=30)
        
        # Query for ingredients expiring within the next 30 days
        query = db.query(
            Inventory.id.label('inventory_id'),
            Ingredient.id.label('ingredient_id'),
            Ingredient.name.label('ingredient_name'),
            Ingredient.category,
            Inventory.quantity,
            Inventory.unit,
            Inventory.expiration_date
        ).join(Ingredient, Inventory.ingredient_id == Ingredient.id)\
         .filter(Inventory.user_id == current_user_id)\
         .filter(Inventory.expiration_date >= today)\
         .filter(Inventory.expiration_date <= thirty_days_later)\
         .order_by(Inventory.expiration_date)
        
        results = query.all()
        
        # Convert to Pydantic model instances
        expiring_ingredients = []
        for row in results:
            expiring_ingredients.append(ExpiringIngredient(
                ingredient_id=row.ingredient_id,
                ingredient_name=row.ingredient_name,
                category=row.category,
                quantity=row.quantity,
                unit=row.unit,
                expiration_date=row.expiration_date.isoformat() if isinstance(row.expiration_date, datetime) else row.expiration_date
            ))
        
        return expiring_ingredients
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
