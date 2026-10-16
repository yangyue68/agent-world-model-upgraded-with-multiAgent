from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class AnimalOut(BaseModel):
    id: int
    name: str
    species: str
    breed: Optional[str] = None
    birth_date: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class VaccineOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MedicalEventOut(BaseModel):
    id: int
    animal_id: int
    event_type: str
    event_date: datetime
    notes: Optional[str] = None
    created_by: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class VaccinationOut(BaseModel):
    id: int
    medical_event_id: int
    vaccine_id: int
    lot_number: str
    next_due_date: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MedicationOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MedicationOrderOut(BaseModel):
    id: int
    animal_id: int
    medication_id: int
    dosage: str
    route: str
    frequency: str
    start_date: datetime
    end_date: datetime
    due_date: datetime
    status: str
    notes: Optional[str] = None
    created_by: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class MedicalEventCreate(BaseModel):
    event_type: str
    event_date: datetime
    notes: Optional[str] = None
    vaccination_details: Optional[dict] = None
    medication_order: Optional[dict] = None

class VaccinationAndMedicationCreate(BaseModel):
    vaccine_name: str
    lot_number: str
    event_date: datetime
    next_due_date: datetime
    medication_name: str
    dosage: str
    route: str
    frequency: str
    start_date: datetime
    end_date: datetime
    due_date: datetime

class MedicationOrderCreate(BaseModel):
    medication_name: str
    dosage: str
    route: str
    frequency: str
    start_date: datetime
    end_date: datetime
    due_date: datetime
    notes: Optional[str] = None