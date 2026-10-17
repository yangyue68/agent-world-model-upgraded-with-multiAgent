import os
from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class GrassType(Base):
    __tablename__ = "grass_types"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Paddock(Base):
    __tablename__ = "paddocks"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    area_hectares = Column(Float, nullable=False)
    grass_type_id = Column(Integer, ForeignKey("grass_types.id"))
    note = Column(Text)
    created_by = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("area_hectares > 0"),)

app = FastAPI()

class CreatePaddockRequest(BaseModel):
    name: str
    area_hectares: float
    grass_type_name: str
    note: Optional[str] = None

class CreatePaddockResponse(BaseModel):
    id: int
    name: str
    area_hectares: float
    grass_type_id: int
    note: str
    created_by: int

@app.post("/paddocks", response_model=CreatePaddockResponse, operation_id="create_paddock")
def create_paddock(request: CreatePaddockRequest):
    db = SessionLocal()
    try:
        # Get the grass type ID
        grass_type = db.query(GrassType).filter(GrassType.name == request.grass_type_name).first()
        if not grass_type:
            raise HTTPException(status_code=400, detail=f"Grass type '{request.grass_type_name}' not found")
        
        # Get current user ID from environment
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
        
        # Create new paddock
        new_paddock = Paddock(
            name=request.name,
            area_hectares=request.area_hectares,
            grass_type_id=grass_type.id,
            note=request.note or "",
            created_by=current_user_id
        )
        
        db.add(new_paddock)
        db.commit()
        db.refresh(new_paddock)
        
        return CreatePaddockResponse(
            id=new_paddock.id,
            name=new_paddock.name,
            area_hectares=new_paddock.area_hectares,
            grass_type_id=new_paddock.grass_type_id,
            note=new_paddock.note,
            created_by=new_paddock.created_by
        )
    finally:
        db.close()
