import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Animal(Base):
    __tablename__ = "animals"
    
    id = Column(Integer, primary_key=True, index=True)
    tag_id = Column(String, unique=True, nullable=False, index=True)
    breed_id = Column(Integer, ForeignKey("breeds.id"), nullable=False)
    sex = Column(String, nullable=False)
    date_of_birth = Column(Date, nullable=False)
    mob_id = Column(Integer, ForeignKey("mobs.id"))
    
    breed = relationship("Breed")
    mob = relationship("Mob")

class Breed(Base):
    __tablename__ = "breeds"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    species = Column(String, nullable=False)

class Mob(Base):
    __tablename__ = "mobs"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)

app = FastAPI()

class AnimalResponse(BaseModel):
    tag_id: str
    breed: str
    sex: str
    date_of_birth: str

@app.get("/animals", response_model=List[AnimalResponse], operation_id="find_animals_by_tag_and_mob")
def find_animals_by_tag_and_mob(
    tag_prefix: str = Query(..., description="Tag ID prefix to filter animals (e.g., 'AUS')"),
    mob_name: str = Query(..., description="Name of the mob to filter animals (e.g., 'Home Paddock')")
):
    db = SessionLocal()
    try:
        # Get mob ID by name
        mob = db.query(Mob).filter(Mob.name == mob_name).first()
        if not mob:
            return []
        
        # Find animals with matching tag prefix and assigned to the specified mob
        animals = db.query(Animal, Breed.name.label('breed_name')).join(Breed).filter(
            Animal.tag_id.startswith(tag_prefix),
            Animal.mob_id == mob.id
        ).all()
        
        results = []
        for animal, breed_name in animals:
            results.append({
                "tag_id": animal.tag_id,
                "breed": breed_name,
                "sex": animal.sex,
                "date_of_birth": animal.date_of_birth.isoformat() if animal.date_of_birth else None
            })
        
        return results
    finally:
        db.close()
