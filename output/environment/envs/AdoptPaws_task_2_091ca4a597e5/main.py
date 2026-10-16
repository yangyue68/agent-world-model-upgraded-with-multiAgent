import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Read DATABASE_PATH at module import
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

# Create SQLAlchemy engine
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="AdoptPaws API", version="1.0.0")


class RabiesGapReportItem(BaseModel):
    animal_id: int
    animal_name: str
    surgery_date: str  # date format YYYY-MM-DD
    rabies_vaccination_status: Optional[str] = None
    last_rabies_vaccine_date: Optional[str] = None  # date format YYYY-MM-DD
    next_due_date: Optional[str] = None  # date format YYYY-MM-DD
    assigned_veterinarian: Optional[str] = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get(
    "/medical-events/rabies-gap-report",
    response_model=List[RabiesGapReportItem],
    operation_id="get_rabies_gap_report",
    tags=["MedicalEvents"],
    summary="Retrieve medical events for animals with spay/neuter but missing completed rabies vaccination",
)
def get_rabies_gap_report():
    """
    Identifies animals that have received a 'Spay/Neuter' surgery and do not have a 'Rabies' 
    vaccination record with status 'Completed'. For these animals, retrieves all medical events 
    from the past 6 months. The response includes animal ID, name, the date of the spay/neuter 
    surgery, the current status of the rabies vaccination (or null if no record exists), the last 
    recorded rabies vaccination date, the next due date for rabies, and the veterinarian assigned 
    to the most recent relevant event or the spay/neuter event.
    
    Note: If an animal has multiple spay/neuter events, the most recent one is used for the surgery 
    date context. If no rabies vaccination record exists at all, status, last vaccine date, and next 
    due date are returned as null.
    """
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        six_months_ago = now - timedelta(days=182)  # approximately 6 months
        six_months_ago_str = six_months_ago.strftime("%Y-%m-%d")

        # Step 1: Find animals that have had a Spay/Neuter event
        # Get the most recent spay/neuter event per animal using a subquery to ensure correct vet_id association
        spay_neuter_query = text("""
            SELECT me.animal_id, me.event_date as surgery_date, me.veterinarian_id
            FROM medical_events me
            INNER JOIN (
                SELECT animal_id, MAX(event_date) as max_event_date
                FROM medical_events
                WHERE event_type = 'Spay/Neuter'
                GROUP BY animal_id
            ) latest_sn ON me.animal_id = latest_sn.animal_id AND me.event_date = latest_sn.max_event_date
            WHERE me.event_type = 'Spay/Neuter'
        """)
        spay_neuter_results = db.execute(spay_neuter_query).fetchall()

        if not spay_neuter_results:
            return []

        animal_ids_with_spay_neuter = [row[0] for row in spay_neuter_results]
        spay_neuter_map = {row[0]: {"surgery_date": row[1], "vet_id": row[2]} for row in spay_neuter_results}

        # Step 2: For each animal, check if they have a Rabies vaccination with status 'Completed'
        # Animals that DO NOT have a completed rabies vaccination are our targets
        placeholders = ",".join([f":aid_{i}" for i in range(len(animal_ids_with_spay_neuter))])
        params = {f"aid_{i}": aid for i, aid in enumerate(animal_ids_with_spay_neuter)}

        completed_rabies_query = text(f"""
            SELECT DISTINCT v.animal_id
            FROM vaccinations v
            WHERE v.animal_id IN ({placeholders})
              AND v.vaccine_type = 'Rabies'
              AND v.status = 'Completed'
        """)
        completed_rabies_animals = set(row[0] for row in db.execute(completed_rabies_query, params).fetchall())

        # Animals with spay/neuter but NO completed rabies vaccination
        target_animal_ids = [aid for aid in animal_ids_with_spay_neuter if aid not in completed_rabies_animals]

        if not target_animal_ids:
            return []

        # Step 3: For target animals, gather all data needed
        target_placeholders = ",".join([f":tid_{i}" for i in range(len(target_animal_ids))])
        target_params = {f"tid_{i}": aid for i, aid in enumerate(target_animal_ids)}

        # Get animal names
        animals_query = text(f"""
            SELECT id, name
            FROM animals
            WHERE id IN ({target_placeholders})
        """)
        animals_rows = db.execute(animals_query, target_params).fetchall()
        animal_names = {row[0]: row[1] for row in animals_rows}

        # Get rabies vaccination info (any status, not just completed) for target animals
        # We need: status, vaccination_date, next_due_date for the most recent rabies record
        rabies_info_query = text(f"""
            SELECT v.animal_id, v.status, v.vaccination_date, v.next_due_date, v.veterinarian_id
            FROM vaccinations v
            WHERE v.animal_id IN ({target_placeholders})
              AND v.vaccine_type = 'Rabies'
            ORDER BY v.animal_id, v.vaccination_date DESC
        """)
        rabies_rows = db.execute(rabies_info_query, target_params).fetchall()

        # Build map of latest rabies info per animal
        rabies_info_map = {}
        for row in rabies_rows:
            aid = row[0]
            if aid not in rabies_info_map:
                rabies_info_map[aid] = {
                    "status": row[1],
                    "vaccination_date": row[2],
                    "next_due_date": row[3],
                    "vet_id": row[4],
                }

        # Get veterinarians for target animals' relevant events
        # First, get all medical events for target animals in the past 6 months
        medical_events_query = text(f"""
            SELECT me.id, me.animal_id, me.event_type, me.event_date, me.veterinarian_id
            FROM medical_events me
            WHERE me.animal_id IN ({target_placeholders})
              AND me.event_date >= :six_months_ago
            ORDER BY me.animal_id, me.event_date DESC
        """)
        med_events_params = dict(target_params)
        med_events_params["six_months_ago"] = six_months_ago_str
        med_events_rows = db.execute(medical_events_query, med_events_params).fetchall()

        # For each target animal, find the most recent relevant event's veterinarian
        # "relevant event" means either the spay/neuter event or any medical event in past 6 months
        # The spec says: "assigned_veterinarian: the veterinarian assigned to the most recent relevant event or the spay/neuter event"
        # So we take the most recent event overall (including spay/neuter) for each animal
        
        # Actually, let's re-read: "assigned_veterinarian: the veterinarian assigned to the most recent relevant event or the spay/neuter event"
        # This suggests: look at all medical events for the animal in the past 6 months, plus the spay/neuter event itself,
        # and pick the vet from the most recent one.
        
        # Let's also include the spay/neuter event even if it's older than 6 months, since it defines the surgery date
        # But for determining the assigned vet, we consider the most recent event among those in past 6 months OR the spay/neuter event
        
        # Simpler approach: for each target animal, get ALL their medical events ordered by date desc,
        # and take the first one that has a veterinarian_id. That's the "most recent relevant event."
        
        all_med_events_for_targets_query = text(f"""
            SELECT me.animal_id, me.event_date, me.veterinarian_id
            FROM medical_events me
            WHERE me.animal_id IN ({target_placeholders})
            ORDER BY me.animal_id, me.event_date DESC
        """)
        all_med_events_rows = db.execute(all_med_events_for_targets_query, target_params).fetchall()

        # Build map: for each animal, the vet from the most recent event that has a vet_id
        most_recent_vet_map = {}
        for row in all_med_events_rows:
            aid = row[0]
            if aid not in most_recent_vet_map and row[2] is not None:
                most_recent_vet_map[aid] = row[2]

        # Get veterinarian names
        all_vet_ids = set()
        for aid in target_animal_ids:
            if aid in most_recent_vet_map:
                all_vet_ids.add(most_recent_vet_map[aid])
            if aid in spay_neuter_map and spay_neuter_map[aid]["vet_id"]:
                all_vet_ids.add(spay_neuter_map[aid]["vet_id"])
            if aid in rabies_info_map and rabies_info_map[aid]["vet_id"]:
                all_vet_ids.add(rabies_info_map[aid]["vet_id"])

        vet_names_map = {}
        if all_vet_ids:
            vet_placeholders = ",".join([f":vid_{i}" for i in range(len(all_vet_ids))])
            vet_params = {f"vid_{i}": vid for i, vid in enumerate(all_vet_ids)}
            vets_query = text(f"""
                SELECT id, name
                FROM veterinarians
                WHERE id IN ({vet_placeholders})
            """)
            vets_rows = db.execute(vets_query, vet_params).fetchall()
            vet_names_map = {row[0]: row[1] for row in vets_rows}

        # Build final results
        results = []
        for aid in target_animal_ids:
            animal_name = animal_names.get(aid)
            if animal_name is None:
                continue

            sn_info = spay_neuter_map.get(aid)
            if sn_info is None:
                continue

            surgery_date = sn_info["surgery_date"]
            if surgery_date is None:
                continue

            # Format surgery_date
            if isinstance(surgery_date, datetime):
                surgery_date_str = surgery_date.strftime("%Y-%m-%d")
            else:
                surgery_date_str = str(surgery_date)

            ri = rabies_info_map.get(aid)
            if ri:
                rabies_status = ri["status"]
                last_rabies_date = ri["vaccination_date"]
                next_due = ri["next_due_date"]
                
                if isinstance(last_rabies_date, datetime):
                    last_rabies_date_str = last_rabies_date.strftime("%Y-%m-%d")
                elif last_rabies_date is not None:
                    last_rabies_date_str = str(last_rabies_date)
                else:
                    last_rabies_date_str = None
                
                if isinstance(next_due, datetime):
                    next_due_str = next_due.strftime("%Y-%m-%d")
                elif next_due is not None:
                    next_due_str = str(next_due)
                else:
                    next_due_str = None
            else:
                rabies_status = None
                last_rabies_date_str = None
                next_due_str = None

            # Determine assigned veterinarian
            # Use the most recent event's vet, falling back to spay/neuter vet if none found
            assigned_vet_id = most_recent_vet_map.get(aid)
            if assigned_vet_id is None and sn_info["vet_id"]:
                assigned_vet_id = sn_info["vet_id"]
            
            if assigned_vet_id is not None:
                assigned_vet_name = vet_names_map.get(assigned_vet_id)
            else:
                assigned_vet_name = None

            item = RabiesGapReportItem(
                animal_id=aid,
                animal_name=animal_name,
                surgery_date=surgery_date_str,
                rabies_vaccination_status=rabies_status,
                last_rabies_vaccine_date=last_rabies_date_str,
                next_due_date=next_due_str,
                assigned_veterinarian=assigned_vet_name,
            )
            results.append(item)

        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        db.close()
