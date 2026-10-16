from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import date

class ApplicationBulkAssignRequest(BaseModel):
    program_name: str
    intake_term: str
    intake_year: int
    intake_level: str
    reviewer_email: str
    rubric_name: str
    rubric_version: str
    review_deadline: date
    comment_note: str

    model_config = ConfigDict(from_attributes=True)

class ApplicationBulkAssignResponse(BaseModel):
    assigned_count: int
    failed_assignments: List[dict]

    model_config = ConfigDict(from_attributes=True)