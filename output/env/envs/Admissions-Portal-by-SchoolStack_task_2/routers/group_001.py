from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from typing import Optional
from datetime import datetime

from database import get_db
from models import Applicant, Intake, Interview
from schemas import ApplicantsResponse

router = APIRouter(tags=["Applicants"])


@router.get("/api/applicants", response_model=ApplicantsResponse, operation_id="list_applicants")
async def list_applicants(
    db: Session = Depends(get_db),
    intake_id: Optional[int] = Query(None, description="Optional intake ID to filter applicants. If provided, intake_term, intake_year, and intake_program are ignored for filtering."),
    intake_term: Optional[str] = Query(None, description="Optional intake term filter, matched against intakes.term."),
    intake_year: Optional[int] = Query(None, description="Optional intake year filter, matched against intakes.year."),
    intake_program: Optional[str] = Query(None, description="Optional intake program filter, matched against intakes.program."),
    min_gpa: Optional[float] = Query(None, description="Minimum applicants.gpa, inclusive. For the task, use 3.5."),
    decision_flag: Optional[str] = Query(None, description="Exact applicants.decision_flag filter. Allowed values are Pending, Accepted, Rejected, Waitlist.", enum=["Pending", "Accepted", "Rejected", "Waitlist"]),
    has_scheduled_interview: Optional[bool] = Query(None, description="When true, include only applicants with at least one interviews row whose status is Scheduled. When false, exclude applicants with at least one such interview. When omitted, do not filter on interviews."),
    sort_by: Optional[str] = Query("submission_date", description="Sort field. Only submission_date is supported for this task. Defaults to submission_date.", enum=["submission_date"]),
    sort_order: Optional[str] = Query("desc", description="Sort order for sort_by. Use desc for application submission date descending. Defaults to desc.", enum=["asc", "desc"])
):
    
    # Start building the query
    query = db.query(Applicant)
    
    # Join with intakes if needed
    if intake_id is not None:
        query = query.filter(Applicant.intake_id == intake_id)
    elif any([intake_term, intake_year, intake_program]):
        query = query.join(Intake, Applicant.intake_id == Intake.id)
        if intake_term:
            query = query.filter(Intake.term == intake_term)
        if intake_year:
            query = query.filter(Intake.year == intake_year)
        if intake_program:
            query = query.filter(Intake.program == intake_program)
    
    # Apply GPA filter
    if min_gpa is not None:
        query = query.filter(Applicant.gpa >= min_gpa)
    
    # Apply decision flag filter
    if decision_flag is not None:
        query = query.filter(Applicant.decision_flag == decision_flag)
    
    # Apply interview filter
    if has_scheduled_interview is not None:
        subquery = db.query(Interview.applicant_id).filter(
            Interview.status == "Scheduled"
        ).subquery()
        
        if has_scheduled_interview:
            query = query.filter(Applicant.id.in_(subquery))
        else:
            query = query.filter(~Applicant.id.in_(subquery))
    
    # Apply sorting
    if sort_by == "submission_date":
        if sort_order == "desc":
            query = query.order_by(Applicant.submission_date.desc())
        else:
            query = query.order_by(Applicant.submission_date.asc())
    
    # Execute query and return results
    applicants = query.distinct(Applicant.id).all()
    
    return ApplicantsResponse(applicants=applicants)