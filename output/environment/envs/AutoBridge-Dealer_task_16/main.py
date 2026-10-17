import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Path, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Part(Base):
    __tablename__ = "parts"
    
    id = Column(Integer, primary_key=True, index=True)
    part_number = Column(String, unique=True, nullable=False)
    description = Column(Text, nullable=False)
    reorder_point = Column(Integer, nullable=False, default=0)
    bin_location = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("reorder_point >= 0", name="check_reorder_point"),
    )

class PartUpdate(Base):
    __tablename__ = "part_updates"
    
    id = Column(Integer, primary_key=True, index=True)
    part_id = Column(Integer, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    field_name = Column(String, nullable=False)
    old_value = Column(Text)
    new_value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class PartResponse(BaseModel):
    id: int
    part_number: str
    description: str
    reorder_point: int
    bin_location: str

class PartUpdateRequest(BaseModel):
    reorder_point: int
    bin_location: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/parts/{part_id}", response_model=PartResponse, operation_id="update_part_reorder_point_and_bin_location")
def update_part_reorder_point_and_bin_location(
    part_id: int = Path(..., description="Internal ID of the part to be updated"),
    update_data: PartUpdateRequest = None,
    db: Session = Depends(get_db)
):
    # Get current part
    part = db.query(Part).filter(Part.id == part_id).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    
    # Store old values for logging
    old_reorder_point = part.reorder_point
    old_bin_location = part.bin_location
    
    # Update part
    part.reorder_point = update_data.reorder_point
    part.bin_location = update_data.bin_location
    part.updated_at = datetime.utcnow()
    
    # Log changes
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Create update records for reorder_point
    if old_reorder_point != update_data.reorder_point:
        part_update = PartUpdate(
            part_id=part.id,
            user_id=current_user_id,
            field_name="reorder_point",
            old_value=str(old_reorder_point),
            new_value=str(update_data.reorder_point)
        )
        db.add(part_update)
    
    # Create update record for bin_location
    if old_bin_location != update_data.bin_location:
        part_update = PartUpdate(
            part_id=part.id,
            user_id=current_user_id,
            field_name="bin_location",
            old_value=old_bin_location,
            new_value=update_data.bin_location
        )
        db.add(part_update)
    
    db.commit()
    db.refresh(part)
    
    return PartResponse(
        id=part.id,
        part_number=part.part_number,
        description=part.description,
        reorder_point=part.reorder_point,
        bin_location=part.bin_location
    )

@app.get("/parts", response_model=list[PartResponse], operation_id="find_parts_by_part_number")
def find_parts_by_part_number(
    part_number: str,
    db: Session = Depends(get_db)
):
    parts = db.query(Part).filter(Part.part_number == part_number).all()
    return [
        PartResponse(
            id=part.id,
            part_number=part.part_number,
            description=part.description,
            reorder_point=part.reorder_point,
            bin_location=part.bin_location
        ) for part in parts
    ]
