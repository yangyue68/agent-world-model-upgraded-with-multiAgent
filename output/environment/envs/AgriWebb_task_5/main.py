import os
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define models based on the schema
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

class Paddock(Base):
    __tablename__ = "paddocks"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    area_hectares = Column(Float, nullable=False)
    grass_type = Column(String)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

class Mob(Base):
    __tablename__ = "mobs"
    id = Column(Integer, primary_key=True, index=True)
    mob_code = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

class MobMovement(Base):
    __tablename__ = "mob_movements"
    id = Column(Integer, primary_key=True, index=True)
    mob_id = Column(Integer, ForeignKey("mobs.id"), nullable=False)
    paddock_id = Column(Integer, ForeignKey("paddocks.id"), nullable=False)
    move_date = Column(Date, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

# Pydantic models for request/response
class PaddockUpdate(BaseModel):
    area_hectares: float
    grass_type: str

class MobMovementCreate(BaseModel):
    mob_id: int
    paddock_id: int
    move_date: date

class PaddockResponse(BaseModel):
    id: int
    name: str
    area_hectares: float
    grass_type: str

class MobMovementResponse(BaseModel):
    id: int
    mob_id: int
    paddock_id: int
    move_date: date

# Create the app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/paddocks/{paddock_id}", response_model=PaddockResponse, operation_id="update_paddock")
def update_paddock(paddock_id: int, paddock_update: PaddockUpdate):
    db = next(get_db())
    
    # Find the paddock by ID
    paddock = db.query(Paddock).filter(Paddock.id == paddock_id).first()
    if not paddock:
        raise HTTPException(status_code=404, detail="Paddock not found")
    
    # Update the paddock
    paddock.area_hectares = paddock_update.area_hectares
    paddock.grass_type = paddock_update.grass_type
    db.commit()
    db.refresh(paddock)
    
    return paddock

@app.post("/mob_movements", response_model=MobMovementResponse, operation_id="create_mob_movement")
def create_mob_movement(mob_movement: MobMovementCreate):
    db = next(get_db())
    
    # Verify that the mob exists
    mob = db.query(Mob).filter(Mob.id == mob_movement.mob_id).first()
    if not mob:
        raise HTTPException(status_code=404, detail="Mob not found")
    
    # Verify that the paddock exists
    paddock = db.query(Paddock).filter(Paddock.id == mob_movement.paddock_id).first()
    if not paddock:
        raise HTTPException(status_code=404, detail="Paddock not found")
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Create the mob movement
    new_movement = MobMovement(
        mob_id=mob_movement.mob_id,
        paddock_id=mob_movement.paddock_id,
        move_date=mob_movement.move_date,
        created_by=current_user_id
    )
    
    db.add(new_movement)
    db.commit()
    db.refresh(new_movement)
    
    return new_movement

# Task execution endpoint
@app.post("/execute_task", operation_id="execute_task")
def execute_task():
    db = next(get_db())
    
    # Find the paddock named 'North Paddock'
    north_paddock = db.query(Paddock).filter(Paddock.name == "North Paddock").first()
    if not north_paddock:
        raise HTTPException(status_code=404, detail="North Paddock not found")
    
    # Update the paddock area and grass type
    north_paddock.area_hectares = 120.0
    north_paddock.grass_type = "Perennial Ryegrass"
    db.commit()
    db.refresh(north_paddock)
    
    # Find mob 'MOB-05'
    mob_05 = db.query(Mob).filter(Mob.mob_code == "MOB-05").first()
    if not mob_05:
        raise HTTPException(status_code=404, detail="Mob MOB-05 not found")
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Create mob movement
    new_movement = MobMovement(
        mob_id=mob_05.id,
        paddock_id=north_paddock.id,
        move_date=date(2025, 4, 1),
        created_by=current_user_id
    )
    
    db.add(new_movement)
    db.commit()
    db.refresh(new_movement)
    
    return {
        "message": "Task executed successfully",
        "paddock_updated": north_paddock.id,
        "mob_moved": new_movement.id
    }
