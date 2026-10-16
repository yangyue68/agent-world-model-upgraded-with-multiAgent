from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import MedicalEvent, Vaccination, MedicationOrder, Vaccine, Medication, User, Animal
from schemas import VaccinationAndMedicationCreate, MedicalEventOut, VaccinationOut, MedicationOrderOut
from datetime import datetime

router = APIRouter(
    prefix="/api/animals",
    tags=["Vaccinations"]
)


@router.post("/{animal_id}/vaccinations", operation_id="log_vaccination_and_medication", response_model=dict)
async def log_vaccination_and_medication(animal_id: int, payload: VaccinationAndMedicationCreate, db: Session = Depends(get_db)):
    # Verify animal exists
    animal = db.query(Animal).filter(Animal.id == animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    # Find or create vaccine
    vaccine = db.query(Vaccine).filter(Vaccine.name == payload.vaccine_name).first()
    if not vaccine:
        vaccine = Vaccine(
            name=payload.vaccine_name,
            description=f"Vaccine for {payload.vaccine_name}",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(vaccine)
        db.flush()

    # Find or create medication
    medication = db.query(Medication).filter(Medication.name == payload.medication_name).first()
    if not medication:
        medication = Medication(
            name=payload.medication_name,
            description=f"Medication for {payload.medication_name}",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(medication)
        db.flush()

    # Create medical event
    medical_event = MedicalEvent(
        animal_id=animal_id,
        event_type="vaccination",
        event_date=payload.event_date,
        notes="Vaccination event with medication order",
        created_by=1,  # Assuming current user is user ID 1
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(medical_event)
    db.flush()  # Get the ID without committing

    # Create vaccination record
    vaccination = Vaccination(
        medical_event_id=medical_event.id,
        vaccine_id=vaccine.id,
        lot_number=payload.lot_number,
        next_due_date=payload.next_due_date,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(vaccination)
    db.flush()

    # Create medication order
    medication_order = MedicationOrder(
        animal_id=animal_id,
        medication_id=medication.id,
        dosage=payload.dosage,
        route=payload.route,
        frequency=payload.frequency,
        start_date=payload.start_date,
        end_date=payload.end_date,
        due_date=payload.due_date,
        status="active",
        notes="Prescribed medication for vaccination",
        created_by=1,  # Assuming current user is user ID 1
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(medication_order)
    db.flush()

    db.commit()

    return {
        "medical_event_id": medical_event.id,
        "vaccination_id": vaccination.id,
        "medication_order_id": medication_order.id
    }