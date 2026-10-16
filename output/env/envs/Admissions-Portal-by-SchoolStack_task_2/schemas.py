from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class ApplicantOut(BaseModel):
    id: int
    user_id: int
    intake_id: int
    gpa: float
    decision_flag: str
    submission_date: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicantsResponse(BaseModel):
    applicants: List[ApplicantOut]