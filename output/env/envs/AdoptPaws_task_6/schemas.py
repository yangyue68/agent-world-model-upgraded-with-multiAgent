from pydantic import BaseModel, ConfigDict
from datetime import date
from typing import Optional

class VaccinationUpdate(BaseModel):
    administered_date: Optional[date] = None
    lot_number: Optional[str] = None
    next_due_date: Optional[date] = None
    model_config = ConfigDict(from_attributes=True)

class MedicationDiscontinue(BaseModel):
    end_date: Optional[date] = None
    discontinued_reason: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class MessageResponse(BaseModel):
    message: str
    model_config = ConfigDict(from_attributes=True)