import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = "sqlite:///./test.db"

# Strip the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH.replace("sqlite:///", "")

# Create engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

class MedicalEvent(BaseModel):
    animal_id: int
    animal_name: str
    surgery_date: str
    rabies_vaccination_status: Optional[str] = None
    last_rabies_vaccine_date: Optional[str] = None
    next_due_date: Optional[str] = None
    assigned_veterinarian: Optional[str] = None

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/medical-events/rabies-gap-report", response_model=List[MedicalEvent], operation_id="get_rabies_gap_report")
def get_rabies_gap_report(db = Depends(get_db)):
    # Calculate the date 6 months ago from today
    six_months_ago = datetime.now() - timedelta(days=180)
    today = datetime.now()
    
    # SQL query to find animals with spay/neuter but missing completed rabies vaccination
    # and retrieve all medical events from the past 6 months
    sql_query = """
    WITH animals_with_spay_neuter AS (
        SELECT DISTINCT animal_id, MAX(event_date) as surgery_date
        FROM medical_events
        WHERE event_type = 'Spay/Neuter'
        GROUP BY animal_id
    ),
    animals_without_completed_rabies AS (
        SELECT DISTINCT awsn.animal_id, awsn.surgery_date
        FROM animals_with_spay_neuter awsn
        LEFT JOIN vaccinations v ON awsn.animal_id = v.animal_id 
            AND v.vaccine_type = 'Rabies' 
            AND v.status = 'Completed'
        WHERE v.id IS NULL
    )
    SELECT 
        a.id as animal_id,
        a.name as animal_name,
        aws.surgery_date,
        v.status as rabies_vaccination_status,
        v.vaccination_date as last_rabies_vaccine_date,
        v.next_due_date,
        vet.name as assigned_veterinarian
    FROM animals_without_completed_rabies aws
    JOIN animals a ON aws.animal_id = a.id
    LEFT JOIN vaccinations v ON aws.animal_id = v.animal_id 
        AND v.vaccine_type = 'Rabies'
    LEFT JOIN medical_events me ON aws.animal_id = me.animal_id 
        AND me.event_date >= :six_months_ago
        AND me.event_date <= :today
        AND me.event_type != 'Spay/Neuter'  -- Exclude spay/neuter events from the medical events list
    LEFT JOIN veterinarians vet ON me.veterinarian_id = vet.id
    WHERE me.id IS NOT NULL OR EXISTS (
        SELECT 1 FROM medical_events me2 
        WHERE me2.animal_id = aws.animal_id 
        AND me2.event_date >= :six_months_ago 
        AND me2.event_date <= :today
        AND me2.event_type = 'Spay/Neuter'
    )
    ORDER BY a.id, me.event_date DESC
    """
    
    # Execute the query
    result = db.execute(text(sql_query), {
        "six_months_ago": six_months_ago.strftime("%Y-%m-%d"),
        "today": today.strftime("%Y-%m-%d")
    })
    
    # Process results
    records = []
    for row in result.fetchall():
        # Get the most recent veterinarian for the animal's medical events
        vet_name = row.assigned_veterinarian
        
        # If no veterinarian found from medical events, check the spay/neuter event
        if not vet_name:
            # Find the veterinarian who performed the spay/neuter
            spay_neuter_sql = """
            SELECT vet.name
            FROM medical_events me
            JOIN veterinarians vet ON me.veterinarian_id = vet.id
            WHERE me.animal_id = :animal_id AND me.event_type = 'Spay/Neuter'
            ORDER BY me.event_date DESC
            LIMIT 1
            """
            vet_result = db.execute(text(spay_neuter_sql), {"animal_id": row.animal_id})
            vet_row = vet_result.fetchone()
            if vet_row:
                vet_name = vet_row.name
                
        records.append(MedicalEvent(
            animal_id=row.animal_id,
            animal_name=row.animal_name,
            surgery_date=row.surgery_date.strftime("%Y-%m-%d"),
            rabies_vaccination_status=row.rabies_vaccination_status,
            last_rabies_vaccine_date=row.last_rabies_vaccine_date.strftime("%Y-%m-%d") if row.last_rabies_vaccine_date else None,
            next_due_date=row.next_due_date.strftime("%Y-%m-%d") if row.next_due_date else None,
            assigned_veterinarian=vet_name
        ))
    
    return records
