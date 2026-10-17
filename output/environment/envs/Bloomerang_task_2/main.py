import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class Constituent(Base):
    __tablename__ = "constituents"
    
    id = Column(String, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    engagement_score = Column(Integer, CheckConstraint("engagement_score BETWEEN 0 AND 100"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CustomAttribute(Base):
    __tablename__ = "custom_attributes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    constituent_id = Column(String, ForeignKey("constituents.id"), nullable=False)
    attribute_name = Column(String, nullable=False)
    attribute_value = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Note(Base):
    __tablename__ = "notes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    constituent_id = Column(String, ForeignKey("constituents.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    note_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Request Models
class CustomAttributeRequest(BaseModel):
    name: str
    value: str

class UpdateConstituentProfileRequest(BaseModel):
    engagement_score: int
    custom_attributes: Optional[List[CustomAttributeRequest]] = []
    note_text: Optional[str] = None

class AddCustomAttributeRequest(BaseModel):
    name: str
    value: str

class AddNoteRequest(BaseModel):
    note_text: str

# Response Models
class UpdateConstituentProfileResponse(BaseModel):
    constituent_id: str
    message: str

class AddCustomAttributeResponse(BaseModel):
    custom_attribute_id: int
    message: str

class AddNoteResponse(BaseModel):
    note_id: int
    message: str

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/constituents/{constituent_id}", response_model=UpdateConstituentProfileResponse, operation_id="update_constituent_profile")
def update_constituent_profile(
    constituent_id: str,
    payload: UpdateConstituentProfileRequest,
    db: Session = Depends(get_db)
):
    # Validate engagement score
    if not 0 <= payload.engagement_score <= 100:
        raise HTTPException(status_code=400, detail="Engagement score must be between 0 and 100")
    
    # Get the constituent
    constituent = db.query(Constituent).filter(Constituent.id == constituent_id).first()
    if not constituent:
        raise HTTPException(status_code=404, detail="Constituent not found")
    
    # Update engagement score
    constituent.engagement_score = payload.engagement_score
    
    # Handle custom attributes
    for attr in payload.custom_attributes:
        existing_attr = db.query(CustomAttribute).filter(
            CustomAttribute.constituent_id == constituent_id,
            CustomAttribute.attribute_name == attr.name
        ).first()
        
        if existing_attr:
            existing_attr.attribute_value = attr.value
            existing_attr.updated_at = datetime.utcnow()
        else:
            new_attr = CustomAttribute(
                constituent_id=constituent_id,
                attribute_name=attr.name,
                attribute_value=attr.value
            )
            db.add(new_attr)
    
    # Handle note
    if payload.note_text:
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id:
            raise HTTPException(status_code=401, detail="Current user not set")
            
        new_note = Note(
            constituent_id=constituent_id,
            user_id=int(current_user_id),
            note_text=payload.note_text
        )
        db.add(new_note)
    
    db.commit()
    db.refresh(constituent)
    
    return UpdateConstituentProfileResponse(
        constituent_id=constituent.id,
        message="Constituent profile updated successfully"
    )

@app.post("/constituents/{constituent_id}/custom_attributes", response_model=AddCustomAttributeResponse, operation_id="add_constituent_custom_attribute")
def add_constituent_custom_attribute(
    constituent_id: str,
    payload: AddCustomAttributeRequest,
    db: Session = Depends(get_db)
):
    # Get the constituent
    constituent = db.query(Constituent).filter(Constituent.id == constituent_id).first()
    if not constituent:
        raise HTTPException(status_code=404, detail="Constituent not found")
    
    # Check if attribute already exists
    existing_attr = db.query(CustomAttribute).filter(
        CustomAttribute.constituent_id == constituent_id,
        CustomAttribute.attribute_name == payload.name
    ).first()
    
    if existing_attr:
        raise HTTPException(status_code=400, detail="Custom attribute already exists")
    
    # Create new custom attribute
    new_attr = CustomAttribute(
        constituent_id=constituent_id,
        attribute_name=payload.name,
        attribute_value=payload.value
    )
    db.add(new_attr)
    db.commit()
    db.refresh(new_attr)
    
    return AddCustomAttributeResponse(
        custom_attribute_id=new_attr.id,
        message="Custom attribute added successfully"
    )

@app.post("/constituents/{constituent_id}/notes", response_model=AddNoteResponse, operation_id="add_constituent_note")
def add_constituent_note(
    constituent_id: str,
    payload: AddNoteRequest,
    db: Session = Depends(get_db)
):
    # Get the constituent
    constituent = db.query(Constituent).filter(Constituent.id == constituent_id).first()
    if not constituent:
        raise HTTPException(status_code=404, detail="Constituent not found")
    
    # Get current user
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Current user not set")
    
    # Create new note
    new_note = Note(
        constituent_id=constituent_id,
        user_id=int(current_user_id),
        note_text=payload.note_text
    )
    db.add(new_note)
    db.commit()
    db.refresh(new_note)
    
    return AddNoteResponse(
        note_id=new_note.id,
        message="Note added successfully"
    )
