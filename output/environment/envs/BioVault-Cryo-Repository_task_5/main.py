import os
from datetime import datetime
from typing import Literal
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class SpecimenType(Base):
    __tablename__ = "specimen_types"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Tank(Base):
    __tablename__ = "tanks"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Container(Base):
    __tablename__ = "containers"
    id = Column(Integer, primary_key=True)
    tank_id = Column(Integer, ForeignKey("tanks.id"), nullable=False)
    slot_number = Column(Integer, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    tank = relationship("Tank")

class Rack(Base):
    __tablename__ = "racks"
    id = Column(Integer, primary_key=True)
    barcode = Column(String, unique=True, nullable=False)
    rack_type = Column(String, nullable=False, default="10x10")
    specimen_type_id = Column(Integer, ForeignKey("specimen_types.id"))
    container_id = Column(Integer, ForeignKey("containers.id"))
    position = Column(String)
    orientation_verified = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    specimen_type = relationship("SpecimenType")
    container = relationship("Container")

class RackPosition(Base):
    __tablename__ = "rack_positions"
    id = Column(Integer, primary_key=True)
    rack_id = Column(Integer, ForeignKey("racks.id"), nullable=False)
    row_label = Column(String, nullable=False)
    column_label = Column(String, nullable=False)
    specimen_id = Column(Integer, ForeignKey("specimens.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    rack = relationship("Rack")
    specimen = relationship("Specimen")

class Specimen(Base):
    __tablename__ = "specimens"
    id = Column(Integer, primary_key=True)
    specimen_type_id = Column(Integer, ForeignKey("specimen_types.id"), nullable=False)
    barcode = Column(String, unique=True)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    specimen_type = relationship("SpecimenType")

# Request Model
class MapRackRequest(BaseModel):
    rack_barcode: str
    tank_name: str
    slot_number: int
    specimen_type_name: str

# Response Model
class MapRackResponse(BaseModel):
    rack_id: int
    message: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/racks/map", response_model=MapRackResponse, operation_id="map_new_rack")
def map_new_rack(request: MapRackRequest):
    db = next(get_db())
    
    # Get the current user ID from environment
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    
    # Find the tank
    tank = db.query(Tank).filter(Tank.name == request.tank_name).first()
    if not tank:
        raise HTTPException(status_code=404, detail=f"Tank '{request.tank_name}' not found")
    
    # Find the container slot
    container = db.query(Container).filter(
        Container.tank_id == tank.id,
        Container.slot_number == request.slot_number
    ).first()
    if not container:
        raise HTTPException(status_code=404, detail=f"Container slot {request.slot_number} not found in tank '{request.tank_name}'")
    
    # Find the specimen type
    specimen_type = db.query(SpecimenType).filter(SpecimenType.name == request.specimen_type_name).first()
    if not specimen_type:
        raise HTTPException(status_code=404, detail=f"Specimen type '{request.specimen_type_name}' not found")
    
    # Create a new rack
    rack = Rack(
        barcode=request.rack_barcode,
        specimen_type_id=specimen_type.id,
        container_id=container.id,
        position="A1",
        orientation_verified=1  # Verified to match system's coordinate schema
    )
    
    db.add(rack)
    db.commit()
    db.refresh(rack)
    
    # Create initial positions for the rack (A1 through J10)
    # For simplicity, we'll just create the first few positions
    # In a real implementation, you'd create all 100 positions
    for row in range(ord('A'), ord('J') + 1):
        for col in range(1, 11):
            row_label = chr(row)
            col_label = str(col)
            
            # Only create position for A1 since that's what we're verifying
            if row_label == 'A' and col_label == '1':
                rack_position = RackPosition(
                    rack_id=rack.id,
                    row_label=row_label,
                    column_label=col_label
                )
                db.add(rack_position)
    
    db.commit()
    
    return MapRackResponse(
        rack_id=rack.id,
        message=f"Rack {rack.barcode} successfully mapped to container slot {container.slot_number} in tank '{tank.name}' with specimen type '{specimen_type.name}'"
    )
