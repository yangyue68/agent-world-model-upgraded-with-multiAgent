import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Mob(Base):
    __tablename__ = "mobs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Animal(Base):
    __tablename__ = "animals"
    id = Column(Integer, primary_key=True, index=True)
    tag_id = Column(String, unique=True, nullable=False)
    breed = Column(String)
    sex = Column(String, check_constraint=text("sex IN ('male', 'female')"))
    birth_date = Column(Date)
    mob_id = Column(Integer, ForeignKey("mobs.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

app = FastAPI()

class DeleteAnimalResponse(BaseModel):
    message: str
    deleted_animal_id: int

@app.delete("/animals/{tag_id}", response_model=DeleteAnimalResponse, operation_id="delete_animal_record")
def delete_animal_record(tag_id: str):
    db = SessionLocal()
    try:
        animal = db.query(Animal).filter(Animal.tag_id == tag_id).first()
        if not animal:
            raise HTTPException(status_code=404, detail="Animal not found")
        
        animal_id = animal.id
        
        # Delete the animal record
        db.delete(animal)
        db.commit()
        
        return DeleteAnimalResponse(
            message=f"Successfully deleted animal with tag ID {tag_id}",
            deleted_animal_id=animal_id
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
