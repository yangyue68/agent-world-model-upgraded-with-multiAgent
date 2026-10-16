from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import SessionLocal, get_db
from models import AdoptionApplication, Applicant, HomeCheck, Animal, User
from schemas import (
    AdoptionApplicationIn,
    AdoptionApplicationWithHomeCheckResponse,
    HomeCheckIn,
    ApplicantIn,
)
from datetime import datetime

router = APIRouter(tags=["AdoptionApplications"])


def get_db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post(
    "/adoption_applications",
    response_model=AdoptionApplicationWithHomeCheckResponse,
    operation_id="create_adoption_application_with_home_check",
)
async def create_adoption_application_with_home_check(
    payload: AdoptionApplicationIn,
    current_user: User = Depends(lambda: None),  # Placeholder for auth
    db: Session = Depends(get_db),
):
    # Validate animal availability
    animal = db.query(Animal).filter(Animal.id == payload.animal_id).first()
    if not animal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Animal not found."
        )
    if animal.status != "Available":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Animal is not available for adoption."
        )

    # Check if applicant exists by email
    applicant = db.query(Applicant).filter(Applicant.email == payload.applicant.email).first()
    if not applicant:
        # Create new applicant
        applicant = Applicant(**payload.applicant.model_dump())
        db.add(applicant)
        db.flush()  # Get the ID without committing

    # Create adoption application
    adoption_app = AdoptionApplication(
        animal_id=payload.animal_id,
        applicant_id=applicant.id,
        status="Pending Home Check",
        desired_meet_greet_date=payload.desired_meet_greet_date,
        created_by_user_id=current_user.id if current_user and hasattr(current_user, 'id') else 1,  # Default fallback
    )
    db.add(adoption_app)
    db.flush()  # Get the ID without committing

    # Create home check
    home_check = HomeCheck(
        application_id=adoption_app.id,
        scheduled_date=payload.home_check_scheduled_date,
        status="Scheduled",
    )
    db.add(home_check)

    db.commit()

    # Refresh objects to get all fields
    db.refresh(adoption_app)
    db.refresh(home_check)

    return {
        "adoption_application": adoption_app,
        "home_check": home_check,
    }