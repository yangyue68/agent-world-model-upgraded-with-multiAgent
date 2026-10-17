import os
from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException

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
    display_name = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class SavedSearch(Base):
    __tablename__ = "saved_searches"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    query = Column(String, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    max_price = Column(Float, CheckConstraint("max_price IS NULL OR max_price >= 0"))
    email_alerts_enabled = Column(Integer, CheckConstraint("email_alerts_enabled IN (0, 1)"), nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# Pydantic models
class SavedSearchCreate(BaseModel):
    name: str
    query: str
    category_id: int
    max_price: Optional[float] = None
    email_alerts_enabled: Optional[bool] = False

class SavedSearchResponse(SavedSearchCreate):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

# FastAPI app
app = FastAPI()

# Helper functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/saved_searches", response_model=SavedSearchResponse, operation_id="create_saved_search")
def create_saved_search(search: SavedSearchCreate):
    db = next(get_db())
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if current_user_id == 0:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    # Verify category exists
    category = db.query(Category).filter(Category.id == search.category_id).first()
    if not category:
        raise HTTPException(status_code=400, detail="Invalid category_id")
    
    # Create saved search
    db_search = SavedSearch(
        user_id=current_user_id,
        name=search.name,
        query=search.query,
        category_id=search.category_id,
        max_price=search.max_price,
        email_alerts_enabled=1 if search.email_alerts_enabled else 0
    )
    
    db.add(db_search)
    db.commit()
    db.refresh(db_search)
    
    # Convert email_alerts_enabled back to boolean for response
    response = SavedSearchResponse(
        id=db_search.id,
        user_id=db_search.user_id,
        name=db_search.name,
        query=db_search.query,
        category_id=db_search.category_id,
        max_price=db_search.max_price,
        email_alerts_enabled=bool(db_search.email_alerts_enabled),
        created_at=db_search.created_at,
        updated_at=db_search.updated_at
    )
    
    return response
