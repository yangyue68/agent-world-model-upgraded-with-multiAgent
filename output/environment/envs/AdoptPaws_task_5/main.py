import os
from datetime import date, datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Text, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Animal(Base):
    __tablename__ = 'animals'
    id = Column(Integer, primary_key=True)
    microchip_id = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    species = Column(String)
    breed = Column(String)
    status = Column(String, nullable=False)
    temperament = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add constraint for status
    __table_args__ = (
        CheckConstraint("status IN ('Available', 'Medical Treatment', 'Foster Care', 'Adopted', 'Euthanized', 'Transferred')", name="check_animal_status"),
    )

class AnimalNote(Base):
    __tablename__ = 'animal_notes'
    id = Column(Integer, primary_key=True)
    animal_id = Column(Integer, ForeignKey('animals.id'), nullable=False)
    note = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    animal = relationship("Animal")
    user = relationship("User")

class FosterParent(Base):
    __tablename__ = 'foster_parents'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True)
    full_name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)
    address = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User")

class FosterPlacement(Base):
    __tablename__ = 'foster_placements'
    id = Column(Integer, primary_key=True)
    animal_id = Column(Integer, ForeignKey('animals.id'), nullable=False)
    foster_parent_id = Column(Integer, ForeignKey('foster_parents.id'), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date)
    status = Column(String, default='Active')
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add constraint for status
    __table_args__ = (
        CheckConstraint("status IN ('Active', 'Completed', 'Cancelled')", name="check_placement_status"),
    )
    
    animal = relationship("Animal")
    foster_parent = relationship("FosterParent")

class FosterSupplyRequest(Base):
    __tablename__ = 'foster_supply_requests'
    id = Column(Integer, primary_key=True)
    foster_placement_id = Column(Integer, ForeignKey('foster_placements.id'), nullable=False)
    status = Column(String, default='Pending')
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Add constraint for status
    __table_args__ = (
        CheckConstraint("status IN ('Pending', 'Approved', 'Fulfilled', 'Cancelled')", name="check_request_status"),
    )
    
    foster_placement = relationship("FosterPlacement")

class FosterSupplyItem(Base):
    __tablename__ = 'foster_supply_items'
    id = Column(Integer, primary_key=True)
    supply_request_id = Column(Integer, ForeignKey('foster_supply_requests.id'), nullable=False)
    item_name = Column(String, nullable=False)
    quantity = Column(Integer, default=1)
    unit = Column(String)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    supply_request = relationship("FosterSupplyRequest")

# Pydantic models
class AnimalUpdate(BaseModel):
    status: Optional[str] = None
    temperament: Optional[str] = None

class AnimalNoteCreate(BaseModel):
    note: str

class FosterParentSearch(BaseModel):
    full_name: Optional[str] = None

class FosterPlacementCreate(BaseModel):
    animal_id: int
    foster_parent_id: int
    start_date: date

class FosterSupplyRequestUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None

class FosterSupplyItemCreate(BaseModel):
    item_name: str
    quantity: Optional[int] = 1
    unit: Optional[str] = None
    notes: Optional[str] = None

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/animals", response_model=List[dict], operation_id="find_animals")
def find_animals(microchip_id: Optional[str] = None, name: Optional[str] = None):
    db = next(get_db())
    query = db.query(Animal)
    if microchip_id:
        query = query.filter(Animal.microchip_id == microchip_id)
    if name:
        query = query.filter(Animal.name.ilike(f"%{name}%"))
    animals = query.all()
    return [
        {
            "id": animal.id,
            "microchip_id": animal.microchip_id,
            "name": animal.name,
            "species": animal.species,
            "breed": animal.breed,
            "status": animal.status,
            "temperament": animal.temperament
        }
        for animal in animals
    ]

@app.patch("/animals/{animal_id}", response_model=dict, operation_id="update_animal")
def update_animal(animal_id: int, animal_update: AnimalUpdate):
    db = next(get_db())
    animal = db.query(Animal).filter(Animal.id == animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")
    
    if animal_update.status is not None:
        animal.status = animal_update.status
    if animal_update.temperament is not None:
        animal.temperament = animal_update.temperament
    
    animal.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(animal)
    
    return {
        "id": animal.id,
        "microchip_id": animal.microchip_id,
        "name": animal.name,
        "status": animal.status,
        "temperament": animal.temperament,
        "updated_at": animal.updated_at
    }

@app.post("/animals/{animal_id}/notes", response_model=dict, operation_id="create_animal_note")
def create_animal_note(animal_id: int, note_create: AnimalNoteCreate):
    db = next(get_db())
    animal = db.query(Animal).filter(Animal.id == animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")
    
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        current_user_id = None
    
    note = AnimalNote(
        animal_id=animal_id,
        note=note_create.note,
        created_by=current_user_id
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    
    return {
        "id": note.id,
        "animal_id": note.animal_id,
        "note": note.note,
        "created_at": note.created_at
    }

@app.get("/foster-parents", response_model=List[dict], operation_id="find_foster_parents")
def find_foster_parents(full_name: Optional[str] = None):
    db = next(get_db())
    query = db.query(FosterParent)
    if full_name:
        query = query.filter(FosterParent.full_name.ilike(f"%{full_name}%"))
    foster_parents = query.all()
    return [
        {
            "id": fp.id,
            "user_id": fp.user_id,
            "full_name": fp.full_name,
            "phone": fp.phone,
            "email": fp.email,
            "address": fp.address
        }
        for fp in foster_parents
    ]

@app.post("/foster-placements", response_model=dict, operation_id="create_foster_placement")
def create_foster_placement(placement_create: FosterPlacementCreate):
    db = next(get_db())
    animal = db.query(Animal).filter(Animal.id == placement_create.animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")
    
    foster_parent = db.query(FosterParent).filter(FosterParent.id == placement_create.foster_parent_id).first()
    if not foster_parent:
        raise HTTPException(status_code=404, detail="Foster parent not found")
    
    placement = FosterPlacement(
        animal_id=placement_create.animal_id,
        foster_parent_id=placement_create.foster_parent_id,
        start_date=placement_create.start_date
    )
    db.add(placement)
    db.commit()
    db.refresh(placement)
    
    return {
        "id": placement.id,
        "animal_id": placement.animal_id,
        "foster_parent_id": placement.foster_parent_id,
        "start_date": placement.start_date,
        "status": placement.status,
        "created_at": placement.created_at
    }

@app.get("/foster-placements/{placement_id}", response_model=dict, operation_id="get_foster_placement")
def get_foster_placement(placement_id: int):
    db = next(get_db())
    placement = db.query(FosterPlacement).filter(FosterPlacement.id == placement_id).first()
    if not placement:
        raise HTTPException(status_code=404, detail="Foster placement not found")
    
    return {
        "id": placement.id,
        "animal_id": placement.animal_id,
        "foster_parent_id": placement.foster_parent_id,
        "start_date": placement.start_date,
        "end_date": placement.end_date,
        "status": placement.status
    }

@app.get("/foster-supply-requests", response_model=List[dict], operation_id="find_foster_supply_requests")
def find_foster_supply_requests(foster_placement_id: int):
    db = next(get_db())
    requests = db.query(FosterSupplyRequest).filter(FosterSupplyRequest.foster_placement_id == foster_placement_id).all()
    return [
        {
            "id": req.id,
            "foster_placement_id": req.foster_placement_id,
            "status": req.status,
            "notes": req.notes,
            "created_at": req.created_at,
            "updated_at": req.updated_at
        }
        for req in requests
    ]

@app.patch("/foster-supply-requests/{request_id}", response_model=dict, operation_id="update_foster_supply_request")
def update_foster_supply_request(request_id: int, request_update: FosterSupplyRequestUpdate):
    db = next(get_db())
    request = db.query(FosterSupplyRequest).filter(FosterSupplyRequest.id == request_id).first()
    if not request:
        raise HTTPException(status_code=404, detail="Foster supply request not found")
    
    if request_update.status is not None:
        request.status = request_update.status
    if request_update.notes is not None:
        request.notes = request_update.notes
    
    request.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(request)
    
    return {
        "id": request.id,
        "foster_placement_id": request.foster_placement_id,
        "status": request.status,
        "notes": request.notes,
        "updated_at": request.updated_at
    }

@app.get("/foster-supply-requests/{request_id}/items", response_model=List[dict], operation_id="get_foster_supply_items")
def get_foster_supply_items(request_id: int):
    db = next(get_db())
    items = db.query(FosterSupplyItem).filter(FosterSupplyItem.supply_request_id == request_id).all()
    return [
        {
            "id": item.id,
            "supply_request_id": item.supply_request_id,
            "item_name": item.item_name,
            "quantity": item.quantity,
            "unit": item.unit,
            "notes": item.notes
        }
        for item in items
    ]

@app.post("/foster-supply-requests/{request_id}/items", response_model=dict, operation_id="add_foster_supply_item")
def add_foster_supply_item(request_id: int, item_create: FosterSupplyItemCreate):
    db = next(get_db())
    request = db.query(FosterSupplyRequest).filter(FosterSupplyRequest.id == request_id).first()
    if not request:
        raise HTTPException(status_code=404, detail="Foster supply request not found")
    
    item = FosterSupplyItem(
        supply_request_id=request_id,
        item_name=item_create.item_name,
        quantity=item_create.quantity,
        unit=item_create.unit,
        notes=item_create.notes
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    
    return {
        "id": item.id,
        "supply_request_id": item.supply_request_id,
        "item_name": item.item_name,
        "quantity": item.quantity,
        "unit": item.unit,
        "notes": item.notes,
        "created_at": item.created_at
    }

# Main task handler
@app.post("/task_5", response_model=dict)
def execute_task_5():
    db = next(get_db())
    
    # Find animal Bella by microchip ID
    animal = db.query(Animal).filter(Animal.microchip_id == "985112004567890").first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal Bella not found")
    
    # Update animal status to 'Foster Care'
    animal.status = "Foster Care"
    animal.temperament = "Gentle, shy"
    animal.updated_at = datetime.utcnow()
    db.commit()
    
    # Add note
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    note = AnimalNote(
        animal_id=animal.id,
        note="Cleared for foster by Dr. Patel on 2025-03-18",
        created_by=current_user_id
    )
    db.add(note)
    db.commit()
    
    # Find foster parent Jamie Rivera
    foster_parent = db.query(FosterParent).filter(FosterParent.full_name == "Jamie Rivera").first()
    if not foster_parent:
        raise HTTPException(status_code=404, detail="Foster parent Jamie Rivera not found")
    
    # Create foster placement
    placement = FosterPlacement(
        animal_id=animal.id,
        foster_parent_id=foster_parent.id,
        start_date=date(2025, 3, 20)
    )
    db.add(placement)
    db.commit()
    db.refresh(placement)
    
    # Find or create foster supply request
    supply_request = db.query(FosterSupplyRequest).filter(FosterSupplyRequest.foster_placement_id == placement.id).first()
    if not supply_request:
        supply_request = FosterSupplyRequest(
            foster_placement_id=placement.id,
            status="Pending"
        )
        db.add(supply_request)
        db.commit()
        db.refresh(supply_request)
    
    # Update supply request status to 'Approved'
    supply_request.status = "Approved"
    supply_request.updated_at = datetime.utcnow()
    db.commit()
    
    # Add supply items
    crate_item = FosterSupplyItem(
        supply_request_id=supply_request.id,
        item_name="Large Crate",
        quantity=1
    )
    db.add(crate_item)
    
    food_item = FosterSupplyItem(
        supply_request_id=supply_request.id,
        item_name="Dry Food",
        quantity=2,
        unit="week"
    )
    db.add(food_item)
    
    db.commit()
    
    return {
        "message": "Task completed successfully",
        "animal_id": animal.id,
        "placement_id": placement.id,
        "supply_request_id": supply_request.id
    }
