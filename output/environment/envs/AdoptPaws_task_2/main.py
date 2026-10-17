import os
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, Depends
from sqlalchemy import create_engine, text
from pydantic import BaseModel

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RabiesGapReportItem(BaseModel):
    animal_id: int
    animal_name: str
    surgery_date: str
    rabies_vaccination_status: Optional[str] = None
    last_rabies_vaccine_date: Optional[str] = None
    next_due_date: Optional[str] = None
    assigned_veterinarian: Optional[str] = None


def get_db():
    with engine.connect() as conn:
        yield conn


@app.get(
    "/medical-events/rabies-gap-report",
    operation_id="get_rabies_gap_report",
    response_model=List[RabiesGapReportItem],
    tags=["MedicalEvents"],
)
def get_rabies_gap_report(db=Depends(get_db)):
    # Calculate date 6 months ago from today
    now = datetime.now()
    six_months_ago = now - timedelta(days=180)
    six_months_ago_str = six_months_ago.strftime("%Y-%m-%d")

    # Step 1: Find animals that have a Spay/Neuter event but do NOT have a completed Rabies vaccination
    # We need to identify animals where:
    # - They have at least one medical_event with event_type = 'Spay/Neuter'
    # - They do NOT have any vaccination record with vaccine_type = 'Rabies' and status = 'Completed'

    query_spay_neuter_animals = text("""
        SELECT DISTINCT me.animal_id
        FROM medical_events me
        WHERE me.event_type = 'Spay/Neuter'
          AND me.animal_id NOT IN (
              SELECT v.animal_id
              FROM vaccinations v
              WHERE v.vaccine_type = 'Rabies'
                AND v.status = 'Completed'
          )
    """)

    result_spay_neuter = db.execute(query_spay_neuter_animals)
    animal_ids_with_gap = [row[0] for row in result_spay_neuter.fetchall()]

    if not animal_ids_with_gap:
        return []

    # For each animal in the gap list, we need:
    # - animal_id, name
    # - most recent spay/neuter surgery date
    # - rabies vaccination status (or null if no record exists)
    # - last rabies vaccine date (or null)
    # - next due date (or null)
    # - assigned veterinarian: from the most recent relevant event or the spay/neuter event

    results = []

    for animal_id in animal_ids_with_gap:
        # Get animal info
        query_animal = text("""
            SELECT id, name
            FROM animals
            WHERE id = :animal_id
        """)
        animal_result = db.execute(query_animal, {"animal_id": animal_id})
        animal_row = animal_result.fetchone()
        if not animal_row:
            continue
        animal_name = animal_row[1]

        # Get most recent spay/neuter surgery date
        query_surgery = text("""
            SELECT event_date, veterinarian_id
            FROM medical_events
            WHERE animal_id = :animal_id
              AND event_type = 'Spay/Neuter'
            ORDER BY event_date DESC
            LIMIT 1
        """)
        surgery_result = db.execute(query_surgery, {"animal_id": animal_id})
        surgery_row = surgery_result.fetchone()
        if not surgery_row:
            continue
        surgery_date = surgery_row[0].strftime("%Y-%m-%d") if hasattr(surgery_row[0], 'strftime') else str(surgery_row[0])
        surgery_vet_id = surgery_row[1]

        # Get rabies vaccination info (any status, not just completed)
        query_rabies_vacc = text("""
            SELECT status, vaccination_date, next_due_date, veterinarian_id
            FROM vaccinations
            WHERE animal_id = :animal_id
              AND vaccine_type = 'Rabies'
            ORDER BY vaccination_date DESC
            LIMIT 1
        """)
        rabies_result = db.execute(query_rabies_vacc, {"animal_id": animal_id})
        rabies_row = rabies_result.fetchone()

        rabies_status = None
        last_rabies_date = None
        next_due = None
        rabies_vet_id = None

        if rabies_row:
            rabies_status = rabies_row[0]
            last_rabies_date = rabies_row[1].strftime("%Y-%m-%d") if hasattr(rabies_row[1], 'strftime') else str(rabies_row[1])
            next_due = rabies_row[2].strftime("%Y-%m-%d") if rabies_row[2] and hasattr(rabies_row[2], 'strftime') else (str(rabies_row[2]) if rabies_row[2] else None)
            rabies_vet_id = rabies_row[3]

        # Determine assigned veterinarian:
        # "the veterinarian assigned to the most recent relevant event or the spay/neuter event"
        # Relevant events include medical events from past 6 months. Let's find the most recent medical event
        # within the past 6 months that has a veterinarian, falling back to the spay/neuter vet.
        query_recent_events = text("""
            SELECT veterinarian_id
            FROM medical_events
            WHERE animal_id = :animal_id
              AND event_date >= :six_months_ago
              AND veterinarian_id IS NOT NULL
            ORDER BY event_date DESC
            LIMIT 1
        """)
        recent_event_result = db.execute(query_recent_events, {
            "animal_id": animal_id,
            "six_months_ago": six_months_ago_str
        })
        recent_event_row = recent_event_result.fetchone()

        assigned_vet_id = None
        if recent_event_row:
            assigned_vet_id = recent_event_row[0]
        elif surgery_vet_id:
            assigned_vet_id = surgery_vet_id
        elif rabies_vet_id:
            assigned_vet_id = rabies_vet_id

        assigned_vet_name = None
        if assigned_vet_id:
            query_vet = text("""
                SELECT name
                FROM veterinarians
                WHERE id = :vet_id
            """)
            vet_result = db.execute(query_vet, {"vet_id": assigned_vet_id})
            vet_row = vet_result.fetchone()
            if vet_row:
                assigned_vet_name = vet_row[0]

        item = RabiesGapReportItem(
            animal_id=animal_id,
            animal_name=animal_name,
            surgery_date=surgery_date,
            rabies_vaccination_status=rabies_status,
            last_rabies_vaccine_date=last_rabies_date,
            next_due_date=next_due,
            assigned_veterinarian=assigned_vet_name,
        )
        results.append(item)

    return results
