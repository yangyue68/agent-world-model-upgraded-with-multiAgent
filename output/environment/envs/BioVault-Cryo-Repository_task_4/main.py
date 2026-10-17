import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

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

class Room(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class CollectionGroup(Base):
    __tablename__ = "collection_groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Freezer(Base):
    __tablename__ = "freezers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    temperature_setpoint = Column(Float, nullable=False)
    install_date = Column(String, nullable=False)
    collection_group_id = Column(Integer, ForeignKey("collection_groups.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Request and response models
class FreezerCreateRequest(BaseModel):
    name: str
    room_name: str
    temperature_setpoint: float
    install_date: str
    collection_group_name: Optional[str] = None

class FreezerResponse(BaseModel):
    id: int
    name: str
    room_id: int
    temperature_setpoint: float
    install_date: str
    collection_group_id: Optional[int] = None

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/freezers", response_model=FreezerResponse, operation_id="create_freezer")
def create_freezer(request: FreezerCreateRequest):
    db = next(get_db())
    
    # Get room ID
    room = db.query(Room).filter(Room.name == request.room_name).first()
    if not room:
        raise HTTPException(status_code=400, detail=f"Room '{request.room_name}' not found")
    
    # Get collection group ID if provided
    collection_group_id = None
    if request.collection_group_name:
        collection_group = db.query(CollectionGroup).filter(CollectionGroup.name == request.collection_group_name).first()
        if not collection_group:
            raise HTTPException(status_code=400, detail=f"Collection group '{request.collection_group_name}' not found")
        collection_group_id = collection_group.id
    
    # Create freezer
    freezer = Freezer(
        name=request.name,
        room_id=room.id,
        temperature_setpoint=request.temperature_setpoint,
        install_date=request.install_date,
        collection_group_id=collection_group_id
    )
    
    db.add(freezer)
    db.commit()
    db.refresh(freezer)
    
    # Return response
    return FreezerResponse(
        id=freezer.id,
        name=freezer.name,
        room_id=freezer.room_id,
        temperature_setpoint=freezer.temperature_setpoint,
        install_date=freezer.install_date,
        collection_group_id=freezer.collection_group_id
    )
