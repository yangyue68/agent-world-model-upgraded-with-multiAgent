import os
from datetime import date, timedelta
from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Animal(Base):
    __tablename__ = "animals"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    species = Column(String, nullable=False)
    breed = Column(String)
    birth_date = Column(Date)
    age_years = Column(Integer)
    temperament = Column(String)
    status = Column(String, nullable=False)
    microchip_id = Column(String, unique=True)
    intake_date = Column(Date, nullable=False)
    current_location = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

# Pydantic models
class AnimalResponse(BaseModel):
    id: int
    name: str
    breed: Optional[str]
    age_years: Optional[int]
    status: str
    microchip_id: Optional[str]
    intake_date: date
    current_location: Optional[str]

# FastAPI app
app = FastAPI()

@app.get("/animals/search", response_model=List[AnimalResponse], operation_id="search_animals_advanced")
def search_animals_advanced(
    species: Optional[str] = Query(None),
    statuses: Optional[List[str]] = Query(None),
    min_age_years: Optional[int] = Query(None),
    temperaments: Optional[List[str]] = Query(None),
    intake_date_before: Optional[date] = Query(None)
):
    db = SessionLocal()
    try:
        query = db.query(Animal)
        
        # Apply filters
        if species:
            query = query.filter(Animal.species == species)
            
        if statuses:
            query = query.filter(Animal.status.in_(statuses))
            
        if min_age_years is not None:
            query = query.filter(Animal.age_years >= min_age_years)
            
        if temperaments:
            query = query.filter(Animal.temperament.in_(temperaments))
            
        if intake_date_before:
            query = query.filter(Animal.intake_date < intake_date_before)
            
        # Execute query
        results = query.all()
        
        # Convert to response model
        return [
            AnimalResponse(
                id=animal.id,
                name=animal.name,
                breed=animal.breed,
                age_years=animal.age_years,
                status=animal.status,
                microchip_id=animal.microchip_id,
                intake_date=animal.intake_date,
                current_location=animal.current_location
            )
            for animal in results
        ]
    finally:
        db.close()
