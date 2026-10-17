import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")  # Placeholder; actual value injected by runtime
engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models for Responses ---

class AnimalResponse(BaseModel):
    id: int
    name: str
    species: str
    breed: Optional[str] = None
    birth_date: Optional[str] = None


class VaccineResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None


class MedicationResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None


class VaccinationEventRequest(BaseModel):
    animal_id: int
    vaccine_id: int
    lot_number: str
    event_date: str
    next_due_date: Optional[str] = None
    notes: Optional[str] = None


class VaccinationEventResponse(BaseModel):
    medical_event_id: int
    vaccination_id: int
    status: str


class MedicationOrderEventRequest(BaseModel):
    animal_id: int
    medication_id: int
    dosage: str
    route: str
    frequency: str
    start_date: str
    end_date: str
    due_date: str
    notes: Optional[str] = None


class MedicationOrderEventResponse(BaseModel):
    medical_event_id: int
    medication_order_id: int
    status: str


# --- Endpoints ---

@app.get("/animals/{animal_id}", response_model=AnimalResponse, operation_id="get_animal_details")
def get_animal_details(animal_id: int = Path(...)):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, name, species, breed, birth_date FROM animals WHERE id = :id"),
            {"id": animal_id}
        ).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Animal not found")
    
    return AnimalResponse(
        id=result[0],
        name=result[1],
        species=result[2],
        breed=result[3],
        birth_date=str(result[4]) if result[4] else None
    )


@app.get("/vaccines", response_model=List[VaccineResponse], operation_id="list_vaccines")
def list_vaccines():
    with engine.connect() as conn:
        results = conn.execute(
            text("SELECT id, name, description FROM vaccines ORDER BY id")
        ).fetchall()
    
    return [
        VaccineResponse(id=r[0], name=r[1], description=r[2])
        for r in results
    ]


@app.post("/medical-events/vaccination", response_model=VaccinationEventResponse, operation_id="create_vaccination_event")
def create_vaccination_event(request: VaccinationEventRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=500, detail="Current user ID not set in environment")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID format")

    with engine.begin() as conn:
        # Verify animal exists
        animal_exists = conn.execute(
            text("SELECT 1 FROM animals WHERE id = :id"),
            {"id": request.animal_id}
        ).fetchone()
        if not animal_exists:
            raise HTTPException(status_code=404, detail="Animal not found")

        # Verify vaccine exists
        vaccine_exists = conn.execute(
            text("SELECT 1 FROM vaccines WHERE id = :id"),
            {"id": request.vaccine_id}
        ).fetchone()
        if not vaccine_exists:
            raise HTTPException(status_code=404, detail="Vaccine not found")

        # Insert medical event
        res_medical = conn.execute(
            text("""
                INSERT INTO medical_events (animal_id, event_type, event_date, notes, created_by)
                VALUES (:animal_id, 'vaccination', :event_date, :notes, :created_by)
            """),
            {
                "animal_id": request.animal_id,
                "event_date": request.event_date,
                "notes": request.notes,
                "created_by": current_user_id
            }
        )
        medical_event_id = res_medical.lastrowid

        # Insert vaccination record
        res_vacc = conn.execute(
            text("""
                INSERT INTO vaccinations (medical_event_id, vaccine_id, lot_number, next_due_date)
                VALUES (:medical_event_id, :vaccine_id, :lot_number, :next_due_date)
            """),
            {
                "medical_event_id": medical_event_id,
                "vaccine_id": request.vaccine_id,
                "lot_number": request.lot_number,
                "next_due_date": request.next_due_date
            }
        )
        vaccination_id = res_vacc.lastrowid

    return VaccinationEventResponse(
        medical_event_id=medical_event_id,
        vaccination_id=vaccination_id,
        status="success"
    )


@app.get("/medications", response_model=List[MedicationResponse], operation_id="list_medications")
def list_medications():
    with engine.connect() as conn:
        results = conn.execute(
            text("SELECT id, name, description FROM medications ORDER BY id")
        ).fetchall()
    
    return [
        MedicationResponse(id=r[0], name=r[1], description=r[2])
        for r in results
    ]


@app.post("/medical-events/medication-order", response_model=MedicationOrderEventResponse, operation_id="create_medication_order_event")
def create_medication_order_event(request: MedicationOrderEventRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=500, detail="Current user ID not set in environment")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID format")

    with engine.begin() as conn:
        # Verify animal exists
        animal_exists = conn.execute(
            text("SELECT 1 FROM animals WHERE id = :id"),
            {"id": request.animal_id}
        ).fetchone()
        if not animal_exists:
            raise HTTPException(status_code=404, detail="Animal not found")

        # Verify medication exists
        med_exists = conn.execute(
            text("SELECT 1 FROM medications WHERE id = :id"),
            {"id": request.medication_id}
        ).fetchone()
        if not med_exists:
            raise HTTPException(status_code=404, detail="Medication not found")

        # Insert medical event
        res_medical = conn.execute(
            text("""
                INSERT INTO medical_events (animal_id, event_type, event_date, notes, created_by)
                VALUES (:animal_id, 'medication', :start_date, :notes, :created_by)
            """),
            {
                "animal_id": request.animal_id,
                "start_date": request.start_date,
                "notes": request.notes,
                "created_by": current_user_id
            }
        )
        medical_event_id = res_medical.lastrowid

        # Insert medication order
        res_order = conn.execute(
            text("""
                INSERT INTO medication_orders 
                (animal_id, medication_id, dosage, route, frequency, start_date, end_date, due_date, status, notes, created_by)
                VALUES 
                (:animal_id, :medication_id, :dosage, :route, :frequency, :start_date, :end_date, :due_date, 'active', :notes, :created_by)
            """),
            {
                "animal_id": request.animal_id,
                "medication_id": request.medication_id,
                "dosage": request.dosage,
                "route": request.route,
                "frequency": request.frequency,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "due_date": request.due_date,
                "notes": request.notes,
                "created_by": current_user_id
            }
        )
        medication_order_id = res_order.lastrowid

    return MedicationOrderEventResponse(
        medical_event_id=medical_event_id,
        medication_order_id=medication_order_id,
        status="success"
    )
