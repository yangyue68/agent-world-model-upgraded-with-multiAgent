import os
from datetime import date, timedelta
from typing import List, Optional
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = "sqlite:////tmp/adopta_paws.db"
DB_FILE_PATH = DATABASE_PATH.replace("sqlite:///", "")

# Create engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Pydantic models
class Animal(BaseModel):
    id: int
    name: str
    breed: Optional[str] = None
    age_years: Optional[int] = None
    status: str
    microchip_id: Optional[str] = None
    intake_date: date
    current_location: Optional[str] = None

# Helper function to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Main endpoint implementation
@app.get("/animals/search", response_model=List[Animal], operation_id="search_animals_advanced")
def search_animals_advanced(
    species: Optional[str] = Query(None),
    statuses: Optional[List[str]] = Query(None),
    min_age_years: Optional[int] = Query(None),
    temperaments: Optional[List[str]] = Query(None),
    intake_date_before: Optional[date] = Query(None),
    db = get_db()
):
    # Build base query
    sql = """
        SELECT id, name, breed, age_years, status, microchip_id, intake_date, current_location
        FROM animals
        WHERE 1=1
    """
    
    params = {}
    
    # Filter by species if provided
    if species:
        sql += " AND species = :species"
        params["species"] = species
    
    # Filter by statuses if provided
    if statuses:
        placeholders = ",".join([f":status_{i}" for i in range(len(statuses))])
        sql += f" AND status IN ({placeholders})"
        for i, status in enumerate(statuses):
            params[f"status_{i}"] = status
    
    # Filter by minimum age if provided
    if min_age_years is not None:
        sql += " AND age_years >= :min_age_years"
        params["min_age_years"] = min_age_years
    
    # Filter by temperaments if provided
    if temperaments:
        placeholders = ",".join([f":temperament_{i}" for i in range(len(temperaments))])
        sql += f" AND temperament IN ({placeholders})"
        for i, temperament in enumerate(temperaments):
            params[f"temperament_{i}"] = temperament
    
    # Filter by intake date before if provided
    if intake_date_before:
        sql += " AND intake_date < :intake_date_before"
        params["intake_date_before"] = intake_date_before
    
    # Execute query
    result = db.execute(text(sql), params)
    
    # Convert to list of Animal objects
    animals = []
    for row in result:
        animal = Animal(
            id=row[0],
            name=row[1],
            breed=row[2],
            age_years=row[3],
            status=row[4],
            microchip_id=row[5],
            intake_date=date.fromisoformat(row[6]),
            current_location=row[7]
        )
        animals.append(animal)
    
    return animals

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "healthy"}
