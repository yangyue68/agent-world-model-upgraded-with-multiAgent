import os
from datetime import date
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, Text, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Animal(Base):
    __tablename__ = "animals"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    microchip_id = Column(String, unique=True, nullable=False)
    species = Column(String)
    breed = Column(String)
    birth_date = Column(Date)
    gender = Column(String)
    description = Column(Text)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class AdoptionApplication(Base):
    __tablename__ = "adoption_applications"
    id = Column(Integer, primary_key=True)
    application_number = Column(String, unique=True, nullable=False)
    animal_id = Column(Integer, ForeignKey("animals.id"))
    applicant_user_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, nullable=False, default="Submitted")
    adoption_fee = Column(Float)
    submitted_at = Column(DateTime, default=func.current_timestamp())
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class HomeCheck(Base):
    __tablename__ = "home_checks"
    id = Column(Integer, primary_key=True)
    adoption_application_id = Column(Integer, ForeignKey("adoption_applications.id"), unique=True)
    completed_date = Column(Date)
    notes = Column(Text)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    adoption_application_id = Column(Integer, ForeignKey("adoption_applications.id"))
    amount = Column(Float, nullable=False)
    payment_method = Column(String, nullable=False)
    payment_date = Column(Date, nullable=False)
    status = Column(String, nullable=False, default="Pending")
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

# Request Models
class LookupAdoptionApplicationQuery(BaseModel):
    application_number: Optional[str] = None
    animal_microchip_id: Optional[str] = None
    animal_name: Optional[str] = None

class UpdateAdoptionApplicationDetailsBody(BaseModel):
    status: Optional[str] = None
    adoption_fee: Optional[float] = None

class CreateOrUpdateHomeCheckBody(BaseModel):
    adoption_application_id: int
    completed_date: date
    notes: Optional[str] = None

class RecordPaymentBody(BaseModel):
    adoption_application_id: int
    amount: float
    payment_method: Literal["Credit Card", "Debit Card", "Cash", "Bank Transfer"]
    payment_date: date
    status: Optional[Literal["Pending", "Paid"]] = "Paid"

# Response Models
class AdoptionApplicationResponse(BaseModel):
    id: int
    application_number: str
    status: str
    adoption_fee: Optional[float]
    animal_id: int
    animal_name: str
    animal_microchip_id: str
    applicant_user_id: int

class UpdateAdoptionApplicationDetailsResponse(BaseModel):
    id: int
    application_number: str
    status: str
    adoption_fee: Optional[float]
    updated_at: str

class CreateOrUpdateHomeCheckResponse(BaseModel):
    id: int
    adoption_application_id: int
    completed_date: date
    notes: Optional[str]
    created_at: str
    updated_at: str

class RecordPaymentResponse(BaseModel):
    id: int
    adoption_application_id: int
    amount: float
    payment_method: str
    payment_date: date
    status: str
    created_at: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/adoption_applications/lookup", response_model=list[AdoptionApplicationResponse], operation_id="lookup_adoption_application")
def lookup_adoption_application(
    application_number: Optional[str] = None,
    animal_microchip_id: Optional[str] = None,
    animal_name: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(
        AdoptionApplication.id,
        AdoptionApplication.application_number,
        AdoptionApplication.status,
        AdoptionApplication.adoption_fee,
        AdoptionApplication.animal_id,
        Animal.name.label("animal_name"),
        Animal.microchip_id.label("animal_microchip_id"),
        AdoptionApplication.applicant_user_id
    ).join(Animal, AdoptionApplication.animal_id == Animal.id)

    if application_number:
        query = query.filter(AdoptionApplication.application_number == application_number)
    if animal_microchip_id:
        query = query.filter(Animal.microchip_id == animal_microchip_id)
    if animal_name:
        query = query.filter(Animal.name == animal_name)

    results = query.all()
    return [
        AdoptionApplicationResponse(
            id=row[0],
            application_number=row[1],
            status=row[2],
            adoption_fee=row[3],
            animal_id=row[4],
            animal_name=row[5],
            animal_microchip_id=row[6],
            applicant_user_id=row[7]
        ) for row in results
    ]

@app.patch("/adoption_applications/{id}", response_model=UpdateAdoptionApplicationDetailsResponse, operation_id="update_adoption_application_details")
def update_adoption_application_details(
    id: int,
    body: UpdateAdoptionApplicationDetailsBody,
    db: Session = Depends(get_db)
):
    application = db.query(AdoptionApplication).filter(AdoptionApplication.id == id).first()
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adoption application not found")

    if body.status is not None:
        application.status = body.status
    if body.adoption_fee is not None:
        application.adoption_fee = body.adoption_fee

    application.updated_at = func.current_timestamp()
    db.commit()
    db.refresh(application)

    return UpdateAdoptionApplicationDetailsResponse(
        id=application.id,
        application_number=application.application_number,
        status=application.status,
        adoption_fee=application.adoption_fee,
        updated_at=application.updated_at.isoformat() if application.updated_at else None
    )

@app.post("/home_checks", response_model=CreateOrUpdateHomeCheckResponse, operation_id="create_or_update_home_check")
def create_or_update_home_check(
    body: CreateOrUpdateHomeCheckBody,
    db: Session = Depends(get_db)
):
    # Check if a home check already exists for this application
    existing_home_check = db.query(HomeCheck).filter(
        HomeCheck.adoption_application_id == body.adoption_application_id
    ).first()

    if existing_home_check:
        # Update existing record
        existing_home_check.completed_date = body.completed_date
        existing_home_check.notes = body.notes
        existing_home_check.updated_at = func.current_timestamp()
        db.commit()
        db.refresh(existing_home_check)
        return CreateOrUpdateHomeCheckResponse(
            id=existing_home_check.id,
            adoption_application_id=existing_home_check.adoption_application_id,
            completed_date=existing_home_check.completed_date,
            notes=existing_home_check.notes,
            created_at=existing_home_check.created_at.isoformat() if existing_home_check.created_at else None,
            updated_at=existing_home_check.updated_at.isoformat() if existing_home_check.updated_at else None
        )
    else:
        # Create new record
        home_check = HomeCheck(
            adoption_application_id=body.adoption_application_id,
            completed_date=body.completed_date,
            notes=body.notes
        )
        db.add(home_check)
        db.commit()
        db.refresh(home_check)
        return CreateOrUpdateHomeCheckResponse(
            id=home_check.id,
            adoption_application_id=home_check.adoption_application_id,
            completed_date=home_check.completed_date,
            notes=home_check.notes,
            created_at=home_check.created_at.isoformat() if home_check.created_at else None,
            updated_at=home_check.updated_at.isoformat() if home_check.updated_at else None
        )

@app.post("/payments", response_model=RecordPaymentResponse, operation_id="record_payment")
def record_payment(
    body: RecordPaymentBody,
    db: Session = Depends(get_db)
):
    payment = Payment(
        adoption_application_id=body.adoption_application_id,
        amount=body.amount,
        payment_method=body.payment_method,
        payment_date=body.payment_date,
        status=body.status
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return RecordPaymentResponse(
        id=payment.id,
        adoption_application_id=payment.adoption_application_id,
        amount=payment.amount,
        payment_method=payment.payment_method,
        payment_date=payment.payment_date,
        status=payment.status,
        created_at=payment.created_at.isoformat() if payment.created_at else None
    )
