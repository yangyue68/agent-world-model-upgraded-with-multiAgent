from fastapi import FastAPI, HTTPException, Path, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime
from typing import Literal

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Animal(Base):
    __tablename__ = "animals"
    
    id = Column(Integer, primary_key=True, index=True)
    tag_id = Column(String, unique=True, nullable=False)
    breed = Column(String, nullable=False)
    sex = Column(String, nullable=False)
    birth_date = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class AnimalUpdateRequest(BaseModel):
    breed: str
    sex: Literal["Male", "Female"]
    birth_date: str

class AnimalResponse(BaseModel):
    id: int
    tag_id: str
    breed: str
    sex: str
    birth_date: str
    created_at: str
    updated_at: str

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/animals/{tag_id}", response_model=AnimalResponse, operation_id="update_animal")
def update_animal(
    tag_id: str = Path(..., description="Unique identifier for the animal"),
    animal_data: AnimalUpdateRequest = None,
    db = Depends(get_db)
):
    db_animal = db.query(Animal).filter(Animal.tag_id == tag_id).first()
    
    if not db_animal:
        raise HTTPException(status_code=404, detail="Animal not found")
    
    # Update the animal fields
    db_animal.breed = animal_data.breed
    db_animal.sex = animal_data.sex
    db_animal.birth_date = animal_data.birth_date
    
    db.commit()
    db.refresh(db_animal)
    
    # Convert datetime objects to strings for response
    return {
        "id": db_animal.id,
        "tag_id": db_animal.tag_id,
        "breed": db_animal.breed,
        "sex": db_animal.sex,
        "birth_date": db_animal.birth_date,
        "created_at": db_animal.created_at.strftime("%Y-%m-%d %H:%M:%S") if db_animal.created_at else None,
        "updated_at": db_animal.updated_at.strftime("%Y-%m-%d %H:%M:%S") if db_animal.updated_at else None
    }
