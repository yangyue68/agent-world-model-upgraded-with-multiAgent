import os
from typing import List

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ApplicantInfo(BaseModel):
    applicant_name: str
    applicant_email: str


@app.get(
    "/applications/under-review",
    response_model=List[ApplicantInfo],
    operation_id="find_under_review_applications",
    tags=["PrimaryResource"],
)
def find_under_review_applications(
    intake_cycle: str = Query(..., description="Intake cycle to filter applications (e.g., 'Fall 2024')"),
):
    query = text("""
        SELECT u.name AS applicant_name, u.email AS applicant_email
        FROM applications a
        JOIN users u ON a.applicant_id = u.id
        WHERE a.status = 'under review' AND a.intake_cycle = :intake_cycle
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"intake_cycle": intake_cycle})
        rows = result.fetchall()
    return [ApplicantInfo(applicant_name=row[0], applicant_email=row[1]) for row in rows]
