import os
from datetime import date, timedelta
from typing import List, Optional
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_URL")
if not DATABASE_PATH or not DATABASE_PATH.startswith("sqlite:///"):
    raise ValueError("Invalid DATABASE_PATH. Must be a valid SQLite URL.")

# Strip the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH[10:]

# Create engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Request model for query parameters
class SearchAnimalsQuery(BaseModel):
    species: Optional[str] = None
    statuses: Optional[List[str]] = None
    min_age_years: Optional[int] = None
    temperaments: Optional[List[str]] = None
    intake_date_before: Optional[date] = None

# Response model for animal data
class AnimalResponse(BaseModel):
    id: int
    name: str
    breed: Optional[str] = None
    age_years: Optional[int] = None
    status: str
    microchip_id: Optional[str] = None
    intake_date: date
    current_location: Optional[str] = None

@app.get("/animals/search", response_model=List[AnimalResponse], operation_id="search_animals_advanced")
def search_animals_advanced(
    species: Optional[str] = Query(None),
    statuses: Optional[List[str]] = Query(None),
    min_age_years: Optional[int] = Query(None),
    temperaments: Optional[List[str]] = Query(None),
    intake_date_before: Optional[date] = Query(None)
):
    # Build the base query
    sql = """
        SELECT id, name, breed, age_years, status, microchip_id, intake_date, current_location
        FROM animals
        WHERE 1=1
    """
    
    params = {}
    
    # Add species filter if provided
    if species:
        sql += " AND species = :species"
        params["species"] = species
    
    # Add statuses filter if provided
    if statuses:
        placeholders = ",".join([f":status_{i}" for i in range(len(statuses))])
        sql += f" AND status IN ({placeholders})"
        for i, status in enumerate(statuses):
            params[f"status_{i}"] = status
    
    # Add minimum age filter if provided
    if min_age_years is not None:
        sql += " AND age_years >= :min_age_years"
        params["min_age_years"] = min_age_years
    
    # Add temperaments filter if provided
    if temperaments:
        placeholders = ",".join([f":temperament_{i}" for i in range(len(temperaments))])
        sql += f" AND temperament IN ({placeholders})"
        for i, temperament in enumerate(temperaments):
            params[f"temperament_{i}"] = temperament
    
    # Add intake date filter if provided
    if intake_date_before:
        sql += " AND intake_date < :intake_date_before"
        params["intake_date_before"] = intake_date_before
    
    # Execute the query
    with SessionLocal() as session:
        result = session.execute(text(sql), params)
        columns = result.keys()
        animals = []
        for row in result.fetchall():
            animal_dict = dict(zip(columns, row))
            animals.append(AnimalResponse(**animal_dict))
        
        return animals
