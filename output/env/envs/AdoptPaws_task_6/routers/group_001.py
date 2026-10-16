from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from models import Animal, Vaccination, Medication
from schemas import VaccinationUpdate, MedicationDiscontinue, MessageResponse
from datetime import datetime

router = APIRouter(tags=["AnimalMedicalRecords"])

@router.patch(
    "/api/animals/{microchip_id}/vaccinations/{vaccine_type}",
    operation_id="update_animal_vaccination",
    response_model=MessageResponse
)
async def update_animal_vaccination(
    microchip_id: str,
    vaccine_type: str,
    payload: VaccinationUpdate,
    db: Session = Depends(get_db)
):
    # Find the animal by microchip_id
    animal = db.query(Animal).filter(Animal.microchip_id == microchip_id).first()
    if not animal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Animal not found")

    # Find the vaccination record
    vaccination = db.query(Vaccination).filter(
        Vaccination.animal_id == animal.id,
        Vaccination.vaccine_type == vaccine_type
    ).first()

    if not vaccination:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vaccination record not found")

    # Update fields if provided
    if payload.administered_date is not None:
        vaccination.administered_date = payload.administered_date
    if payload.lot_number is not None:
        vaccination.lot_number = payload.lot_number
    if payload.next_due_date is not None:
        vaccination.next_due_date = payload.next_due_date

    vaccination.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(vaccination)

    return MessageResponse(message="Vaccination record updated successfully")


@router.patch(
    "/api/animals/{microchip_id}/medications/{medication_name}",
    operation_id="discontinue_animal_medication",
    response_model=MessageResponse
)
async def discontinue_animal_medication(
    microchip_id: str,
    medication_name: str,
    payload: MedicationDiscontinue,
    db: Session = Depends(get_db)
):
    # Find the animal by microchip_id
    animal = db.query(Animal).filter(Animal.microchip_id == microchip_id).first()
    if not animal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Animal not found")

    # Find the medication record
    medication = db.query(Medication).filter(
        Medication.animal_id == animal.id,
        Medication.medication_name == medication_name
    ).first()

    if not medication:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Medication record not found")

    # Update fields
    medication.status = "discontinued"
    if payload.end_date is not None:
        medication.end_date = payload.end_date
    if payload.discontinued_reason is not None:
        medication.discontinued_reason = payload.discontinued_reason

    medication.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(medication)

    return MessageResponse(message="Medication record discontinued successfully")