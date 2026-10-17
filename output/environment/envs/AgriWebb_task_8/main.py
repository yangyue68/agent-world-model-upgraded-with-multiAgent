import os
from datetime import datetime, date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Text, ForeignKey, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Farm(Base):
    __tablename__ = 'farms'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    owner_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Paddock(Base):
    __tablename__ = 'paddocks'
    
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey('farms.id'), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Mob(Base):
    __tablename__ = 'mobs'
    
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey('farms.id'), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class MobMove(Base):
    __tablename__ = 'mob_moves'
    
    id = Column(Integer, primary_key=True)
    mob_id = Column(Integer, ForeignKey('mobs.id'), nullable=False)
    from_paddock_id = Column(Integer, ForeignKey('paddocks.id'))
    to_paddock_id = Column(Integer, ForeignKey('paddocks.id'), nullable=False)
    move_date = Column(Date, nullable=False)
    notes = Column(Text)
    created_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class PaddockResponse(BaseModel):
    id: int
    name: str

class MobResponse(BaseModel):
    id: int
    name: str

class MobMoveResponse(BaseModel):
    id: int
    mob_id: int
    from_paddock_id: Optional[int]
    to_paddock_id: int
    move_date: str  # Changed to str to match expected response format

class DeleteResponse(BaseModel):
    message: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/farms/{farm_id}/paddocks", response_model=List[PaddockResponse], operation_id="find_farm_paddocks")
def find_farm_paddocks(farm_id: int, db = Depends(get_db)):
    paddocks = db.query(Paddock).filter(Paddock.farm_id == farm_id).all()
    return paddocks

@app.get("/farms/{farm_id}/mobs", response_model=List[MobResponse], operation_id="find_farm_mobs")
def find_farm_mobs(farm_id: int, db = Depends(get_db)):
    mobs = db.query(Mob).filter(Mob.farm_id == farm_id).all()
    return mobs

@app.get("/mob_moves", response_model=List[MobMoveResponse], operation_id="find_mob_moves")
def find_mob_moves(
    mob_id: Optional[int] = Query(None),
    from_paddock_id: Optional[int] = Query(None),
    to_paddock_id: Optional[int] = Query(None),
    move_date: Optional[str] = Query(None),
    db = Depends(get_db)
):
    query = db.query(MobMove)
    
    if mob_id is not None:
        query = query.filter(MobMove.mob_id == mob_id)
    if from_paddock_id is not None:
        query = query.filter(MobMove.from_paddock_id == from_paddock_id)
    if to_paddock_id is not None:
        query = query.filter(MobMove.to_paddock_id == to_paddock_id)
    if move_date is not None:
        query = query.filter(MobMove.move_date == move_date)
        
    moves = query.all()
    # Convert move_date to string for proper serialization
    return [
        MobMoveResponse(
            id=move.id,
            mob_id=move.mob_id,
            from_paddock_id=move.from_paddock_id,
            to_paddock_id=move.to_paddock_id,
            move_date=move.move_date.isoformat() if isinstance(move.move_date, date) else str(move.move_date)
        ) for move in moves
    ]

@app.delete("/mob_moves/{move_id}", response_model=DeleteResponse, operation_id="delete_mob_move")
def delete_mob_move(move_id: int, db = Depends(get_db)):
    # Get the current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find the mob move record
    move = db.query(MobMove).filter(MobMove.id == move_id).first()
    
    if not move:
        raise HTTPException(status_code=404, detail="Mob move record not found")
    
    # Verify the user has permission to delete this record
    if move.created_by_user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Permission denied")
    
    # Delete the record
    db.delete(move)
    db.commit()
    
    return {"message": "Mob move record deleted successfully"}
