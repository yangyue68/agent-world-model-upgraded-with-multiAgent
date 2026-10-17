from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, REAL, TEXT, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(TEXT, nullable=False, unique=True)
    full_name = Column(TEXT)
    email = Column(TEXT, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Part(Base):
    __tablename__ = "parts"
    
    id = Column(Integer, primary_key=True, index=True)
    part_number = Column(TEXT, nullable=False, unique=True)
    name = Column(TEXT, nullable=False)
    description = Column(TEXT)
    price = Column(REAL, nullable=False, default=0.0)
    quantity_on_hand = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    discontinued_at = Column(DateTime)
    discontinued_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("price >= 0", name="check_price_non_negative"),
        CheckConstraint("quantity_on_hand >= 0", name="check_quantity_non_negative"),
        CheckConstraint("is_active IN (0, 1)", name="check_is_active_enum"),
    )

app = FastAPI()

class DiscontinuePartRequest(BaseModel):
    discontinued_by: int

class PartResponse(BaseModel):
    id: int
    part_number: str
    name: str
    is_active: int
    discontinued_at: str
    discontinued_by: int

@app.put("/parts/{part_id}/discontinue", response_model=PartResponse, operation_id="discontinue_part")
def discontinue_part(part_id: int, request: DiscontinuePartRequest):
    db = SessionLocal()
    try:
        # Get the current user ID from environment
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
        
        # Verify the user exists
        user = db.query(User).filter(User.id == request.discontinued_by).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user ID")
            
        # Find the part
        part = db.query(Part).filter(Part.id == part_id).first()
        if not part:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Part not found")
            
        # Check if part is already discontinued
        if part.is_active == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Part is already discontinued")
            
        # Update the part
        part.is_active = 0
        part.discontinued_at = datetime.utcnow()
        part.discontinued_by = request.discontinued_by
        
        db.commit()
        db.refresh(part)
        
        return PartResponse(
            id=part.id,
            part_number=part.part_number,
            name=part.name,
            is_active=part.is_active,
            discontinued_at=part.discontinued_at.isoformat() if part.discontinued_at else None,
            discontinued_by=part.discontinued_by
        )
    finally:
        db.close()
