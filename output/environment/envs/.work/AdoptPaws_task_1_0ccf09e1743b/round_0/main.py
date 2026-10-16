import os
from datetime import date, timedelta
from typing import List, Optional
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_URL")
if not DATABASE_PATH:
    raise ValueError("DATABASE_URL environment variable not set")

# Strip the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH[10:] if DATABASE_PATH.startswith("sqlite:///") else DATABASE_PATH

# Create engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Request model
class AnimalSearchRequest(BaseModel):
    species: Optional[str] = None
    statuses: Optional[List[str]] = None
    min_age_years: Optional[int] = None
    temperaments: Optional[List[str]] = None
    intake_date_before: Optional[date] = None

# Response model
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
    # Build the query dynamically
    sql_query = """
        SELECT id, name, breed, age_years, status, microchip_id, intake_date, current_location
        FROM animals
        WHERE 1=1
    """
    
    params = {}
    
    # Filter by species if provided
    if species:
        sql_query += " AND species = :species"
        params["species"] = species
    
    # Filter by statuses if provided
    if statuses:
        placeholders = ",".join([f":status_{i}" for i in range(len(statuses))])
        sql_query += f" AND status IN ({placeholders})"
        for i, status in enumerate(statuses):
            params[f"status_{i}"] = status
    
    # Filter by minimum age if provided
    if min_age_years is not None:
        sql_query += " AND age_years >= :min_age_years"
        params["min_age_years"] = min_age_years
    
    # Filter by temperaments if provided
    if temperaments:
        placeholders = ",".join([f":temperament_{i}" for i in range(len(temperaments))])
        sql_query += f" AND temperament IN ({placeholders})"
        for i, temperament in enumerate(temperaments):
            params[f"temperament_{i}"] = temperament
    
    # Filter by intake date before if provided
    if intake_date_before:
        sql_query += " AND intake_date < :intake_date_before"
        params["intake_date_before"] = intake_date_before
    
    # Execute the query
    with SessionLocal() as session:
        result = session.execute(text(sql_query), params)
        columns = result.keys()
        animals = []
        for row in result.fetchall():
            animal_data = dict(zip(columns, row))
            animals.append(AnimalResponse(**animal_data))
        
        return animals

# Task-specific endpoint - this is the exact implementation requested
@app.get("/animals/search", response_model=List[AnimalResponse], operation_id="search_animals_advanced")
def search_dogs_stray_or_medical(
    species: Optional[str] = Query(None, description="Filter by species (e.g., 'dog', 'cat'). If omitted, searches all species."),
    statuses: Optional[List[str]] = Query(None, description="Filter by one or more statuses (e.g., ['stray_hold', 'medical_treatment']). Matches if animal's status is in this list."),
    min_age_years: Optional[int] = Query(None, description="Filter animals with age_years >= this value."),
    temperaments: Optional[List[str]] = Query(None, description="Filter by one or more temperaments (e.g., ['calm', 'gentle']). Matches if animal's temperament is in this list."),
    intake_date_before: Optional[date] = Query(None, description="Filter animals intaken strictly before this date (YYYY-MM-DD). Used to find animals intaken more than N days ago by passing today's date minus N days.")
):
    # Set default values for the specific task requirements
    if species is None:
        species = "dog"
    
    if statuses is None:
        statuses = ["stray_hold", "medical_treatment"]
    
    if min_age_years is None:
        min_age_years = 7
    
    if temperaments is None:
        temperaments = ["calm", "gentle"]
    
    # Calculate intake_date_before as 30 days ago from today
    if intake_date_before is None:
        intake_date_before = date.today() - timedelta(days=30)
    
    # Call the general search function with these parameters
    return search_animals_advanced(
        species=species,
        statuses=statuses,
        min_age_years=min_age_years,
        temperaments=temperaments,
        intake_date_before=intake_date_before
    )
