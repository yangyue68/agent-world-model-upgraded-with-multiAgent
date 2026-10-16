from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ApplicantIn(BaseModel):
    full_name: str
    email: str
    phone: str
    home_type: str
    existing_pets: str


class ApplicantOut(ApplicantIn):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = dict(from_attributes=True)


class AdoptionApplicationIn(BaseModel):
    animal_id: int
    applicant: ApplicantIn
    desired_meet_greet_date: str
    home_check_scheduled_date: str


class AdoptionApplicationOut(BaseModel):
    id: int
    animal_id: int
    applicant_id: int
    status: str
    desired_meet_greet_date: str
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = dict(from_attributes=True)


class HomeCheckIn(BaseModel):
    application_id: int
    scheduled_date: str
    status: str = "Scheduled"
    notes: Optional[str] = None


class HomeCheckOut(BaseModel):
    id: int
    application_id: int
    scheduled_date: str
    status: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = dict(from_attributes=True)


class AdoptionApplicationWithHomeCheckResponse(BaseModel):
    adoption_application: AdoptionApplicationOut
    home_check: HomeCheckOut