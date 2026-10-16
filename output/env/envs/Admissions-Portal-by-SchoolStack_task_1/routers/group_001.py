from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_
from database import get_db
from models import User, Applicant, Camp, Document
from schemas import SearchApplicantsResponse, ApplicantOut

router = APIRouter(
    prefix="/api",
    tags=["Applicants"]
)


@router.get(
    "/applicants/search",
    operation_id="search_applicants_by_camp_status_and_missing_document",
    response_model=SearchApplicantsResponse
)
async def search_applicants_by_camp_status_and_missing_document(
    camp_name: str = Query(..., description="Exact camp name to match against camps.name.", example="Robotics Camp"),
    camp_term: str = Query(..., description="Exact camp term to match against camps.term.", example="Summer"),
    camp_year: int = Query(..., description="Exact camp year to match against camps.year.", example=2025),
    application_status: str = Query(..., description="Exact applicant application status to match against applicants.application_status. Allowed values are Incomplete, Complete, Submitted, Under Review, Accepted, Rejected, and Waitlisted.", example="Incomplete"),
    missing_document_type: str = Query(..., description="Document type that applicants must be missing. Only applicants with no documents row for the matching applicant_id and this document_type are returned. Allowed values are Recommendation Letter, Transcript, Essay, Resume, and Other.", example="Recommendation Letter"),
    db: Session = Depends(get_db)
):
    # Find the camp
    camp = db.query(Camp).filter(
        and_(
            Camp.name == camp_name,
            Camp.term == camp_term,
            Camp.year == camp_year
        )
    ).first()

    if not camp:
        # Return empty list if no camp found
        return SearchApplicantsResponse(applicants=[])

    # Subquery to find applicants who have the missing document
    subquery = db.query(Document.applicant_id).filter(
        and_(
            Document.applicant_id == Applicant.id,
            Document.document_type == missing_document_type
        )
    ).correlate(Applicant).exists()

    # Main query
    applicants = db.query(User.full_name, User.email).join(
        Applicant, User.id == Applicant.user_id
    ).filter(
        and_(
            Applicant.camp_id == camp.id,
            Applicant.application_status == application_status,
            ~subquery
        )
    ).all()

    # Convert to list of ApplicantOut objects
    result = [
        ApplicantOut(full_name=applicant.full_name, email=applicant.email)
        for applicant in applicants
    ]

    return SearchApplicantsResponse(applicants=result)