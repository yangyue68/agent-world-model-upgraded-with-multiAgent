from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class ApplicantOut(BaseModel):
    full_name: str
    email: str

    model_config = {
        "from_attributes": True
    }


class SearchApplicantsResponse(BaseModel):
    applicants: List[ApplicantOut]