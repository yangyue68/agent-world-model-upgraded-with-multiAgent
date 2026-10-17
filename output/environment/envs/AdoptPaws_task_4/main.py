import os
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, status, Depends
from sqlalchemy import create_engine, Column, Integer, String, REAL, DateTime, ForeignKey, Text, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Animal(Base):
    __tablename__ = "animals"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    species = Column(String, nullable=False)
    breed = Column(String)
    age_years = Column(REAL)
    gender = Column(String)
    description = Column(Text)
    status = Column(String, default="Available")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Applicant(Base):
    __tablename__ = "applicants"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    home_type = Column(String)
    existing_pets = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class AdoptionApplication(Base):
    __tablename__ = "adoption_applications"
    id = Column(Integer, primary_key=True, index=True)
    animal_id = Column(Integer, ForeignKey("animals.id"), nullable=False)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=False)
    status = Column(String, default="Pending")
    desired_meet_greet_date = Column(String)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    animal = relationship("Animal")
    applicant = relationship("Applicant")
    user = relationship("User")

class HomeCheck(Base):
    __tablename__ = "home_checks"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("adoption_applications.id"), nullable=False)
    scheduled_date = Column(String, nullable=False)
    status = Column(String, default="Scheduled")
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    application = relationship("AdoptionApplication")

# Request and Response Models
class CreateAdoptionApplicationRequest(BaseModel):
    animal_id: int
    applicant_email: str
    applicant_full_name: str
    applicant_phone: Optional[str] = None
    applicant_home_type: Optional[str] = None
    applicant_existing_pets: Optional[str] = None
    desired_meet_greet_date: str
    application_status: str
    home_check_scheduled_date: str

class CreateAdoptionApplicationResponse(BaseModel):
    application_id: int
    status: str
    home_check_id: int
    home_check_scheduled_date: str
    animal_id: int
    applicant_id: int

class AnimalDetailsResponse(BaseModel):
    id: int
    name: str
    species: str
    breed: str
    age_years: float
    gender: str
    description: str
    status: str

class ApplicantResponse(BaseModel):
    id: int
    full_name: str
    email: str
    phone: str
    home_type: str
    existing_pets: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/adoption-applications", response_model=CreateAdoptionApplicationResponse, status_code=status.HTTP_201_CREATED, operation_id="create_adoption_application")
def create_adoption_application(request: CreateAdoptionApplicationRequest, db = Depends(get_db)):
    # Validate animal exists
    animal = db.query(Animal).filter(Animal.id == request.animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")
    
    # Find or create applicant
    applicant = db.query(Applicant).filter(Applicant.email == request.applicant_email).first()
    if not applicant:
        applicant = Applicant(
            full_name=request.applicant_full_name,
            email=request.applicant_email,
            phone=request.applicant_phone,
            home_type=request.applicant_home_type,
            existing_pets=request.applicant_existing_pets
        )
        db.add(applicant)
        db.commit()
        db.refresh(applicant)
    else:
        # Update applicant details if provided
        if request.applicant_full_name:
            applicant.full_name = request.applicant_full_name
        if request.applicant_phone:
            applicant.phone = request.applicant_phone
        if request.applicant_home_type:
            applicant.home_type = request.applicant_home_type
        if request.applicant_existing_pets:
            applicant.existing_pets = request.applicant_existing_pets
        db.commit()
        db.refresh(applicant)
    
    # Create adoption application
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    application = AdoptionApplication(
        animal_id=request.animal_id,
        applicant_id=applicant.id,
        status=request.application_status,
        desired_meet_greet_date=request.desired_meet_greet_date,
        created_by_user_id=current_user_id
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    
    # Create home check
    home_check = HomeCheck(
        application_id=application.id,
        scheduled_date=request.home_check_scheduled_date
    )
    db.add(home_check)
    db.commit()
    db.refresh(home_check)
    
    return {
        "application_id": application.id,
        "status": application.status,
        "home_check_id": home_check.id,
        "home_check_scheduled_date": home_check.scheduled_date,
        "animal_id": application.animal_id,
        "applicant_id": application.applicant_id
    }

@app.get("/animals/{animal_id}", response_model=AnimalDetailsResponse, operation_id="get_animal_details")
def get_animal_details(animal_id: int, db = Depends(get_db)):
    animal = db.query(Animal).filter(Animal.id == animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")
    return animal

@app.get("/applicants", response_model=List[ApplicantResponse], operation_id="find_applicants")
def find_applicants(email: Optional[str] = None, full_name: Optional[str] = None, db = Depends(get_db)):
    query = db.query(Applicant)
    if email:
        query = query.filter(Applicant.email == email)
    if full_name:
        query = query.filter(Applicant.full_name.contains(full_name))
    return query.all()
