import os
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

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
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ConditionGrade(Base):
    __tablename__ = "condition_grades"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    rank = Column(Integer, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(String)
    category_id = Column(Integer, ForeignKey("categories.id"))
    condition_grade_id = Column(Integer, ForeignKey("condition_grades.id"))
    starting_price = Column(Numeric(10, 2), nullable=False)
    current_price = Column(Numeric(10, 2))
    auction_start = Column(DateTime)
    auction_end = Column(DateTime)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Watchlist(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    lot_id = Column(Integer, ForeignKey("lots.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Pydantic models
class LotResponse(BaseModel):
    id: int
    title: str
    description: str
    starting_price: float
    condition_grade_name: str

class AddToWatchlistRequest(BaseModel):
    lot_ids: List[int]

class AddToWatchlistResponse(BaseModel):
    success: bool
    added_count: int

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/lots", response_model=List[LotResponse], operation_id="find_qualified_lots")
def find_qualified_lots(
    category_name: str = Query(..., description="Category name to filter by (e.g., 'Antiques')"),
    min_condition_rank: int = Query(..., description="Minimum condition grade rank (e.g., 5 for 'Excellent')")
):
    db = next(get_db())
    
    stmt = select(
        Lot.id,
        Lot.title,
        Lot.description,
        Lot.starting_price,
        ConditionGrade.name.label("condition_grade_name")
    ).select_from(Lot)\
    .join(Category, Lot.category_id == Category.id)\
    .join(ConditionGrade, Lot.condition_grade_id == ConditionGrade.id)\
    .where(
        Category.name == category_name,
        ConditionGrade.rank >= min_condition_rank,
        Lot.status == "active"
    )\
    .order_by(Lot.starting_price.asc())
    
    result = db.execute(stmt).fetchall()
    
    return [
        LotResponse(
            id=row[0],
            title=row[1],
            description=row[2],
            starting_price=float(row[3]),
            condition_grade_name=row[4]
        )
        for row in result
    ]

@app.post("/watchlist", response_model=AddToWatchlistResponse, operation_id="add_lots_to_watchlist")
def add_lots_to_watchlist(request: AddToWatchlistRequest):
    db = next(get_db())
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Check if all lots exist and are active
    lot_ids = request.lot_ids
    stmt = select(Lot.id).where(
        Lot.id.in_(lot_ids),
        Lot.status == "active"
    )
    existing_lots = db.execute(stmt).scalars().all()
    
    if len(existing_lots) != len(lot_ids):
        raise HTTPException(status_code=400, detail="Some lots do not exist or are not active")
    
    # Check if any of these lots are already in the watchlist
    stmt = select(Watchlist.lot_id).where(
        Watchlist.user_id == current_user_id,
        Watchlist.lot_id.in_(lot_ids)
    )
    existing_watchlist_entries = db.execute(stmt).scalars().all()
    
    # Filter out lots that are already in the watchlist
    new_lot_ids = [lid for lid in lot_ids if lid not in existing_watchlist_entries]
    
    # Add new entries to watchlist
    for lot_id in new_lot_ids:
        watchlist_entry = Watchlist(
            user_id=current_user_id,
            lot_id=lot_id
        )
        db.add(watchlist_entry)
    
    db.commit()
    
    return AddToWatchlistResponse(
        success=True,
        added_count=len(new_lot_ids)
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
