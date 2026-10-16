from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import MedicalEvent, Vaccination, MedicationOrder, Vaccine, Medication, User, Animal
from schemas import MedicalEventCreate, MedicalEventOut
from datetime import datetime

router = APIRouter(
    prefix="/api/animals",
    tags=["MedicalEvents"]
)


@router.post("/{animal_id}/medical_events", operation_id="create_medical_event", response_model=MedicalEventOut)
async def create_medical_event(animal_id: int, payload: MedicalEventCreate, db: Session = Depends(get_db)):
    # Verify animal exists
    animal = db.query(Animal).filter(Animal.id == animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    # Create medical event
    medical_event = MedicalEvent(
        animal_id=animal_id,
        event_type=payload.event_type,
        event_date=payload.event_date,
        notes=payload.notes,
        created_by=1,  # Assuming current user is user ID 1
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(medical_event)
    db.flush()  # Get the ID without committing

    vaccination_id = None
    medication_order_id = None

    # Handle vaccination details
    if payload.event_type == "vaccination" and payload.vaccination_details:
        vaccine = db.query(Vaccine).filter(Vaccine.name == payload.vaccination_details["vaccine_name"]).first()
        if not vaccine:
            raise HTTPException(status_code=404, detail="Vaccine not found")

        vaccination = Vaccination(
            medical_event_id=medical_event.id,
            vaccine_id=vaccine.id,
            lot_number=payload.vaccination_details["lot_number"],
            next_due_date=payload.vaccination_details.get("next_due_date"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(vaccination)
        db.flush()
        vaccination_id = vaccination.id

    # Handle medication order
    if payload.event_type == "medication" and payload.medication_order:
        medication = db.query(Medication).filter(Medication.name == payload.medication_order["medication_name"]).first()
        if not medication:
            raise HTTPException(status_code=404, detail="Medication not found")

        medication_order = MedicationOrder(
            animal_id=animal_id,
            medication_id=medication.id,
            dosage=payload.medication_order["dosage"],
            route=payload.medication_order["route"],
            frequency=payload.medication_order["frequency"],
            start_date=payload.medication_order["start_date"],
            end_date=payload.medication_order["end_date"],
            due_date=payload.medication_order["due_date"],
            status="active",
            notes=payload.medication_order.get("notes"),
            created_by=1,  # Assuming current user is user ID 1
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(medication_order)
        db.flush()
        medication_order_id = medication_order.id

    db.commit()
    db.refresh(medical_event)

    return MedicalEventOut.model_validate(medical_event)