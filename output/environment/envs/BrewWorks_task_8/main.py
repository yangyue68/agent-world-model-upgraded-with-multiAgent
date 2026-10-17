import os
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class MaltLot(Base):
    __tablename__ = "malt_lots"
    id = Column(Integer, primary_key=True)
    malt_name = Column(String, nullable=False)
    lot_number = Column(String, unique=True, nullable=False)
    current_volume_lbs = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Batch(Base):
    __tablename__ = "batches"
    id = Column(Integer, primary_key=True)
    batch_code = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    id = Column(Integer, primary_key=True)
    malt_lot_id = Column(Integer, ForeignKey("malt_lots.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    movement_type = Column(String, nullable=False)
    quantity_lbs = Column(Float, nullable=False)
    previous_volume_lbs = Column(Float, nullable=False)
    new_volume_lbs = Column(Float, nullable=False)
    note = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

# Request models
class UpdateMaltLotInventoryRequest(BaseModel):
    batch_id: int
    quantity_lbs: float
    note: str

# Response model
class UpdateMaltLotInventoryResponse(BaseModel):
    success: bool
    message: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/malt-lots/{malt_lot_id}/inventory", response_model=UpdateMaltLotInventoryResponse, operation_id="update_malt_lot_inventory")
def update_malt_lot_inventory(
    malt_lot_id: int,
    payload: UpdateMaltLotInventoryRequest,
    db = Depends(get_db)
):
    # Get the current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    if not current_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    
    # Fetch the malt lot
    malt_lot = db.query(MaltLot).filter(MaltLot.id == malt_lot_id).first()
    if not malt_lot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Malt lot not found")
    
    # Fetch the batch
    batch = db.query(Batch).filter(Batch.id == payload.batch_id).first()
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    
    # Validate that the requested quantity doesn't exceed available volume
    if payload.quantity_lbs > malt_lot.current_volume_lbs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Requested quantity {payload.quantity_lbs} lbs exceeds available volume {malt_lot.current_volume_lbs} lbs"
        )
    
    # Calculate new volume
    new_volume = malt_lot.current_volume_lbs - payload.quantity_lbs
    
    # Record the movement
    movement = InventoryMovement(
        malt_lot_id=malt_lot_id,
        batch_id=payload.batch_id,
        user_id=current_user_id,
        movement_type="decrease",
        quantity_lbs=payload.quantity_lbs,
        previous_volume_lbs=malt_lot.current_volume_lbs,
        new_volume_lbs=new_volume,
        note=payload.note,
    )
    
    # Update the malt lot volume
    malt_lot.current_volume_lbs = new_volume
    
    # Add the movement to the database
    db.add(movement)
    db.commit()
    db.refresh(malt_lot)
    
    return UpdateMaltLotInventoryResponse(
        success=True,
        message=f"Successfully decreased malt lot volume by {payload.quantity_lbs} lbs"
    )
