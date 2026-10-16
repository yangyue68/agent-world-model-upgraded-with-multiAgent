from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime

from database import get_db
from models import AdoptionApplication, HomeCheck, Payment
from schemas import AdoptionApplicationUpdateIn, AdoptionApplicationOut, HomeCheckOut, PaymentOut

router = APIRouter(tags=["AdoptionApplications"])


@router.patch(
    "/api/adoption_applications/{application_number}",
    operation_id="update_adoption_application_approval",
    response_model=AdoptionApplicationOut,
)
async def update_adoption_application_approval(
    application_number: str,
    payload: AdoptionApplicationUpdateIn,
    db: Session = Depends(get_db)
):
    try:
        # Find the adoption application by application_number
        adoption_app = (
            db.query(AdoptionApplication)
            .filter(AdoptionApplication.application_number == application_number)
            .first()
        )

        if not adoption_app:
            raise HTTPException(status_code=404, detail="Adoption application not found")

        # Validate status is 'Approved'
        if payload.status != "Approved":
            raise HTTPException(status_code=400, detail="Status must be 'Approved'")

        # Update adoption application
        adoption_app.status = payload.status
        adoption_app.adoption_fee = payload.adoption_fee
        adoption_app.updated_at = datetime.utcnow()

        # Get or create home check
        home_check = (
            db.query(HomeCheck)
            .filter(HomeCheck.adoption_application_id == adoption_app.id)
            .first()
        )

        if not home_check:
            home_check = HomeCheck(
                adoption_application_id=adoption_app.id,
                completed_date=payload.home_check_completed_date,
                notes=payload.home_check_notes,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(home_check)
        else:
            home_check.completed_date = payload.home_check_completed_date
            home_check.notes = payload.home_check_notes
            home_check.updated_at = datetime.utcnow()

        # Create payment
        payment = Payment(
            adoption_application_id=adoption_app.id,
            amount=payload.payment_amount,
            payment_method=payload.payment_method,
            payment_date=payload.payment_date,
            status="Paid",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(payment)

        db.commit()
        db.refresh(adoption_app)
        db.refresh(home_check)
        db.refresh(payment)

        return AdoptionApplicationOut(
            application_number=adoption_app.application_number,
            status=adoption_app.status,
            adoption_fee=adoption_app.adoption_fee,
            home_check=HomeCheckOut(
                completed_date=home_check.completed_date,
                notes=home_check.notes,
            ),
            payment=PaymentOut(
                amount=payment.amount,
                payment_method=payment.payment_method,
                payment_date=payment.payment_date,
                status=payment.status,
            ),
        )

    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Data integrity error occurred")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
    finally:
        db.close()