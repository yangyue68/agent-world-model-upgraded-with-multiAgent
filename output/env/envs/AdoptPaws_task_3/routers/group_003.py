from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import MedicationOrder, Medication, Animal
from schemas import MedicationOrderCreate, MedicationOrderOut
from datetime import datetime

router = APIRouter(
    prefix="/api/animals",
    tags=["MedicationOrders"]
)


@router.post("/{animal_id}/medication_orders", operation_id="create_medication_order", response_model=MedicationOrderOut)
async def create_medication_order(animal_id: int, payload: MedicationOrderCreate, db: Session = Depends(get_db)):
    # Verify animal exists
    animal = db.query(Animal).filter(Animal.id == animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    # Find medication
    medication = db.query(Medication).filter(Medication.name == payload.medication_name).first()
    if not medication:
        raise HTTPException(status_code=404, detail="Medication not found")

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
        notes=payload.notes,
        created_by=1,  # Assuming current user is user ID 1
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(medication_order)
    db.commit()
    db.refresh(medication_order)

    return MedicationOrderOut.model_validate(medication_order)