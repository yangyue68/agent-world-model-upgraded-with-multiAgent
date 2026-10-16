import os
from datetime import date, timedelta
from typing import List, Optional
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is required")

# Strip the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH.replace("sqlite:///", "")

# Create engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Request model
class SearchAnimalsRequest(BaseModel):
    species: Optional[str] = None
    statuses: Optional[List[str]] = []
    min_age_years: Optional[int] = None
    temperaments: Optional[List[str]] = []
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
    statuses: Optional[List[str]] = Query([]),
    min_age_years: Optional[int] = Query(None),
    temperaments: Optional[List[str]] = Query([]),
    intake_date_before: Optional[date] = Query(None)
):
    # Calculate the date 30 days ago from today
    thirty_days_ago = date.today() - timedelta(days=30)
    
    # Build the query dynamically
    sql = """
        SELECT id, name, breed, age_years, status, microchip_id, intake_date, current_location
        FROM animals
        WHERE species = 'dog'
        AND status IN ('stray_hold', 'medical_treatment')
        AND age_years >= 7
        AND temperament IN ('calm', 'gentle')
        AND intake_date < :thirty_days_ago
    """
    
    params = {
        "thirty_days_ago": thirty_days_ago
    }
    
    # Execute the query
    with SessionLocal() as session:
        result = session.execute(text(sql), params)
        columns = result.keys()
        animals = [dict(zip(columns, row)) for row in result.fetchall()]
    
    # Convert to response objects
    return [AnimalResponse(**animal) for animal in animals]

# Health check endpoint
@app.get("/")
def read_root():
    return {"message": "AdoptPaws API is running"}
