import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Taproom(Base):
    __tablename__ = "taprooms"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    location = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Batch(Base):
    __tablename__ = "batches"
    id = Column(Integer, primary_key=True)
    batch_code = Column(String, nullable=False, unique=True)
    beer_name = Column(String, nullable=False)
    style = Column(String)
    abv = Column(Float)
    ibu = Column(Integer)
    brew_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Tap(Base):
    __tablename__ = "taps"
    id = Column(Integer, primary_key=True)
    taproom_id = Column(Integer, ForeignKey("taprooms.id"), nullable=False)
    tap_number = Column(Integer, nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"))
    pour_size_oz = Column(Integer, nullable=False, default=16)
    price = Column(Float, nullable=False)
    status = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("status IN ('Now Pouring', 'Coming Soon', 'Off Tap', 'Cleaning')"),
        CheckConstraint("price >= 0"),
    )

# Pydantic Models
class TapUpdateRequest(BaseModel):
    batch_code: str
    price: float
    status: str

class TapResponse(BaseModel):
    id: int
    taproom_id: int
    tap_number: int
    batch_id: Optional[int]
    pour_size_oz: int
    price: float
    status: str

class BatchResponse(BaseModel):
    id: int
    batch_code: str
    beer_name: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/taprooms/{taproom_id}/taps/{tap_number}", response_model=TapResponse, operation_id="update_tap")
def update_tap(
    taproom_id: int = Path(..., description="Internal ID of the taproom"),
    tap_number: int = Path(..., description="Tap number within the taproom"),
    tap_update: TapUpdateRequest = None,
    db: Session = Depends(get_db)
):
    # Find the tap
    tap = db.query(Tap).filter(
        Tap.taproom_id == taproom_id,
        Tap.tap_number == tap_number
    ).first()
    
    if not tap:
        raise HTTPException(status_code=404, detail="Tap not found")
    
    # Find the batch by batch_code
    batch = db.query(Batch).filter(Batch.batch_code == tap_update.batch_code).first()
    
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    # Update the tap
    tap.batch_id = batch.id
    tap.price = tap_update.price
    tap.status = tap_update.status
    
    db.commit()
    db.refresh(tap)
    
    return TapResponse(
        id=tap.id,
        taproom_id=tap.taproom_id,
        tap_number=tap.tap_number,
        batch_id=tap.batch_id,
        pour_size_oz=tap.pour_size_oz,
        price=tap.price,
        status=tap.status
    )

@app.get("/batches", response_model=List[BatchResponse], operation_id="find_batches")
def find_batches(
    beer_name: str,
    db: Session = Depends(get_db)
):
    batches = db.query(Batch).filter(Batch.beer_name.like(f"%{beer_name}%")).all()
    return [
        BatchResponse(
            id=batch.id,
            batch_code=batch.batch_code,
            beer_name=batch.beer_name
        ) for batch in batches
    ]

@app.get("/taprooms/{taproom_id}/taps/{tap_number}", response_model=TapResponse, operation_id="get_tap")
def get_tap(
    taproom_id: int = Path(..., description="Internal ID of the taproom"),
    tap_number: int = Path(..., description="Tap number within the taproom"),
    db: Session = Depends(get_db)
):
    tap = db.query(Tap).filter(
        Tap.taproom_id == taproom_id,
        Tap.tap_number == tap_number
    ).first()
    
    if not tap:
        raise HTTPException(status_code=404, detail="Tap not found")
    
    return TapResponse(
        id=tap.id,
        taproom_id=tap.taproom_id,
        tap_number=tap.tap_number,
        batch_id=tap.batch_id,
        pour_size_oz=tap.pour_size_oz,
        price=tap.price,
        status=tap.status
    )
