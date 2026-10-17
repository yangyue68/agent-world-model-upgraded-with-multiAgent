import os
from datetime import datetime
from typing import List, Literal
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
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

class FreezerUnit(Base):
    __tablename__ = "freezer_units"
    id = Column(Integer, primary_key=True, index=True)
    unit_code = Column(String, unique=True, nullable=False)
    unit_name = Column(String)
    temperature_c = Column(Float, nullable=False)
    validated_at = Column(DateTime)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Container(Base):
    __tablename__ = "containers"
    id = Column(Integer, primary_key=True, index=True)
    container_code = Column(String, unique=True, nullable=False)
    container_type = Column(String, nullable=False)
    freezer_unit_id = Column(Integer, ForeignKey("freezer_units.id"))
    capacity = Column(Integer, nullable=False)
    fill_level = Column(Integer, default=0)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    freezer_unit = relationship("FreezerUnit")

class Vial(Base):
    __tablename__ = "vials"
    id = Column(Integer, primary_key=True, index=True)
    vial_code = Column(String, unique=True, nullable=False)
    container_id = Column(Integer, ForeignKey("containers.id"))
    position = Column(String, nullable=False)
    sample_type = Column(String)
    volume_ul = Column(Float)
    concentration_ng_ul = Column(Float)
    status = Column(String, nullable=False, default="stored")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    container = relationship("Container")

class Transfer(Base):
    __tablename__ = "transfers"
    id = Column(Integer, primary_key=True, index=True)
    from_container_id = Column(Integer, ForeignKey("containers.id"))
    to_container_id = Column(Integer, ForeignKey("containers.id"))
    performed_by_user_id = Column(Integer, ForeignKey("users.id"))
    reason = Column(String, nullable=False)
    transferred_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    from_container = relationship("Container", foreign_keys=[from_container_id])
    to_container = relationship("Container", foreign_keys=[to_container_id])
    performed_by_user = relationship("User")

class TransferItem(Base):
    __tablename__ = "transfer_items"
    id = Column(Integer, primary_key=True, index=True)
    transfer_id = Column(Integer, ForeignKey("transfers.id"))
    vial_id = Column(Integer, ForeignKey("vials.id"))
    from_position = Column(String, nullable=False)
    to_position = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    transfer = relationship("Transfer")
    vial = relationship("Vial")

class ContainerFillHistory(Base):
    __tablename__ = "container_fill_history"
    id = Column(Integer, primary_key=True, index=True)
    container_id = Column(Integer, ForeignKey("containers.id"))
    previous_fill_level = Column(Integer, nullable=False)
    new_fill_level = Column(Integer, nullable=False)
    changed_by_user_id = Column(Integer, ForeignKey("users.id"))
    change_reason = Column(String)
    changed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    container = relationship("Container")
    changed_by_user = relationship("User")

# Pydantic Models
class TransferVialsRequest(BaseModel):
    from_container_code: str
    to_container_code: str
    positions: List[str]
    transfer_reason: str

class TransferVialsResponse(BaseModel):
    transfer_id: int
    from_container_id: int
    to_container_id: int
    vials_transferred: List[dict]
    updated_from_container_fill_level: int
    updated_to_container_fill_level: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/transfer-vials", response_model=TransferVialsResponse, operation_id="transfer_vials")
def transfer_vials(request: TransferVialsRequest):
    db = next(get_db())
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Validate containers exist
    from_container = db.query(Container).filter(Container.container_code == request.from_container_code).first()
    if not from_container:
        raise HTTPException(status_code=404, detail="Source container not found")
    
    to_container = db.query(Container).filter(Container.container_code == request.to_container_code).first()
    if not to_container:
        raise HTTPException(status_code=404, detail="Destination container not found")
    
    # Validate freezer units are the same
    if from_container.freezer_unit_id != to_container.freezer_unit_id:
        raise HTTPException(status_code=400, detail="Containers must be in the same freezer unit")
    
    # Validate all positions exist in source container
    vials_to_transfer = []
    for position in request.positions:
        vial = db.query(Vial).filter(
            Vial.container_id == from_container.id,
            Vial.position == position
        ).first()
        
        if not vial:
            raise HTTPException(status_code=400, detail=f"Vial at position {position} not found in source container")
        
        vials_to_transfer.append(vial)
    
    # Create transfer record
    transfer = Transfer(
        from_container_id=from_container.id,
        to_container_id=to_container.id,
        performed_by_user_id=current_user_id,
        reason=request.transfer_reason
    )
    db.add(transfer)
    db.flush()  # Get transfer ID
    
    # Create transfer items
    vials_transferred = []
    for vial in vials_to_transfer:
        # Create transfer item
        transfer_item = TransferItem(
            transfer_id=transfer.id,
            vial_id=vial.id,
            from_position=vial.position,
            to_position=vial.position  # In this case, we're just moving within the same container
        )
        db.add(transfer_item)
        
        # Update vial position to destination container
        vial.container_id = to_container.id
        vial.position = vial.position  # Keep same position
        
        # Add to response data
        vials_transferred.append({
            "vial_code": vial.vial_code,
            "from_position": vial.position,
            "to_position": vial.position
        })
    
    # Update fill levels
    # Decrease from container fill level
    old_from_fill_level = from_container.fill_level
    new_from_fill_level = old_from_fill_level - len(vials_to_transfer)
    from_container.fill_level = new_from_fill_level
    
    # Increase to container fill level
    old_to_fill_level = to_container.fill_level
    new_to_fill_level = old_to_fill_level + len(vials_to_transfer)
    to_container.fill_level = new_to_fill_level
    
    # Record fill level changes
    fill_history_from = ContainerFillHistory(
        container_id=from_container.id,
        previous_fill_level=old_from_fill_level,
        new_fill_level=new_from_fill_level,
        changed_by_user_id=current_user_id,
        change_reason="Transfer out",
    )
    db.add(fill_history_from)
    
    fill_history_to = ContainerFillHistory(
        container_id=to_container.id,
        previous_fill_level=old_to_fill_level,
        new_fill_level=new_to_fill_level,
        changed_by_user_id=current_user_id,
        change_reason="Transfer in",
    )
    db.add(fill_history_to)
    
    db.commit()
    
    return {
        "transfer_id": transfer.id,
        "from_container_id": from_container.id,
        "to_container_id": to_container.id,
        "vials_transferred": vials_transferred,
        "updated_from_container_fill_level": new_from_fill_level,
        "updated_to_container_fill_level": new_to_fill_level
    }
