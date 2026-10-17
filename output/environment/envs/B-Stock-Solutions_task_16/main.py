import os
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, status, Depends
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Text, text, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Lot(Base):
    __tablename__ = "lots"
    id = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    reserve_price = Column(Float, nullable=False)
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ManifestItem(Base):
    __tablename__ = "manifest_items"
    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(String, nullable=False)
    sku = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    condition = Column(String, nullable=False)
    retail_value = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class LotUpdateRequest(BaseModel):
    reserve_price: float
    end_date: str

class ManifestItemRequest(BaseModel):
    sku: str
    quantity: int
    condition: str
    retail_value: float

class DuplicateLotRequest(BaseModel):
    new_title: str
    new_start_date: str
    new_end_date: str

class LotResponse(BaseModel):
    id: str
    title: str
    reserve_price: float
    start_date: str
    end_date: str

class ManifestItemResponse(BaseModel):
    id: int
    lot_id: str
    sku: str
    quantity: int
    condition: str
    retail_value: float

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/lots/{lot_id}", response_model=LotResponse, operation_id="update_lot")
def update_lot(lot_id: str, lot_update: LotUpdateRequest, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Update lot details
    db.query(Lot).filter(Lot.id == lot_id).update({
        Lot.reserve_price: lot_update.reserve_price,
        Lot.end_date: lot_update.end_date,
        Lot.updated_at: datetime.utcnow()
    })
    
    db.commit()
    
    # Fetch updated lot
    updated_lot = db.query(Lot).filter(Lot.id == lot_id).first()
    if not updated_lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lot not found")
    
    return LotResponse(
        id=updated_lot.id,
        title=updated_lot.title,
        reserve_price=updated_lot.reserve_price,
        start_date=updated_lot.start_date,
        end_date=updated_lot.end_date
    )

@app.post("/lots/{lot_id}/manifest_items", response_model=ManifestItemResponse, operation_id="add_manifest_item")
def add_manifest_item(lot_id: str, manifest_item: ManifestItemRequest, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Create new manifest item
    new_item = ManifestItem(
        lot_id=lot_id,
        sku=manifest_item.sku,
        quantity=manifest_item.quantity,
        condition=manifest_item.condition,
        retail_value=manifest_item.retail_value
    )
    
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    
    return ManifestItemResponse(
        id=new_item.id,
        lot_id=new_item.lot_id,
        sku=new_item.sku,
        quantity=new_item.quantity,
        condition=new_item.condition,
        retail_value=new_item.retail_value
    )

@app.post("/lots/{lot_id}/duplicate", response_model=LotResponse, operation_id="duplicate_lot")
def duplicate_lot(lot_id: str, duplicate_request: DuplicateLotRequest, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Get original lot
    original_lot = db.query(Lot).filter(Lot.id == lot_id).first()
    if not original_lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Original lot not found")
    
    # Create new lot with updated details
    new_lot = Lot(
        id=f"LOT-{int(datetime.now().timestamp())}",
        title=duplicate_request.new_title,
        reserve_price=original_lot.reserve_price,
        start_date=duplicate_request.new_start_date,
        end_date=duplicate_request.new_end_date,
        created_by=current_user_id
    )
    
    db.add(new_lot)
    db.commit()
    db.refresh(new_lot)
    
    # Copy manifest items
    manifest_items = db.query(ManifestItem).filter(ManifestItem.lot_id == lot_id).all()
    for item in manifest_items:
        new_manifest_item = ManifestItem(
            lot_id=new_lot.id,
            sku=item.sku,
            quantity=item.quantity,
            condition=item.condition,
            retail_value=item.retail_value
        )
        db.add(new_manifest_item)
    
    db.commit()
    
    return LotResponse(
        id=new_lot.id,
        title=new_lot.title,
        reserve_price=new_lot.reserve_price,
        start_date=new_lot.start_date,
        end_date=new_lot.end_date
    )
