import os
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import re

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    term = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Applicant(Base):
    __tablename__ = "applicants"
    id = Column(Integer, primary_key=True, index=True)
    applicant_code = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    phone = Column(String)
    mailing_address = Column(String)
    city = Column(String)
    state = Column(String)
    postal_code = Column(String)
    program_id = Column(Integer, ForeignKey("programs.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ApplicantNote(Base):
    __tablename__ = "applicant_notes"
    id = Column(Integer, primary_key=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    note = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class ApplicantResponse(BaseModel):
    id: int
    applicant_code: str
    full_name: str
    phone: str | None
    mailing_address: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    program_id: int
    program_name: str
    term: str

class ApplicantUpdate(BaseModel):
    phone: str | None = None
    mailing_address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None

class ApplicantNoteRequest(BaseModel):
    note: str

class ApplicantNoteResponse(BaseModel):
    id: int
    applicant_id: int
    user_id: int
    note: str
    created_at: str

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/applicants/lookup", response_model=ApplicantResponse, operation_id="find_applicant_by_code")
def find_applicant_by_code(
    applicant_code: str,
    program_name: str | None = None,
    term: str | None = None,
    db: SessionLocal = Depends(get_db)
):
    query = db.query(Applicant, Program.name, Program.term)\
        .join(Program, Applicant.program_id == Program.id)\
        .filter(Applicant.applicant_code == applicant_code)
    
    if program_name:
        query = query.filter(Program.name == program_name)
    if term:
        query = query.filter(Program.term == term)
        
    result = query.first()
    
    if not result:
        raise HTTPException(status_code=404, detail="Applicant not found")
        
    applicant, program_name, term = result
    return ApplicantResponse(
        id=applicant.id,
        applicant_code=applicant.applicant_code,
        full_name=applicant.full_name,
        phone=applicant.phone,
        mailing_address=applicant.mailing_address,
        city=applicant.city,
        state=applicant.state,
        postal_code=applicant.postal_code,
        program_id=applicant.program_id,
        program_name=program_name,
        term=term
    )

@app.patch("/applicants/{applicant_id}", response_model=ApplicantResponse, operation_id="update_applicant_details")
def update_applicant_details(
    applicant_id: int,
    update_data: ApplicantUpdate,
    db: SessionLocal = Depends(get_db)
):
    applicant = db.query(Applicant).filter(Applicant.id == applicant_id).first()
    
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
        
    update_fields = {}
    if update_data.phone is not None:
        update_fields["phone"] = update_data.phone
    if update_data.mailing_address is not None:
        update_fields["mailing_address"] = update_data.mailing_address
    if update_data.city is not None:
        update_fields["city"] = update_data.city
    if update_data.state is not None:
        update_fields["state"] = update_data.state
    if update_data.postal_code is not None:
        update_fields["postal_code"] = update_data.postal_code
        
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")
        
    update_fields["updated_at"] = datetime.utcnow()
    db.query(Applicant).filter(Applicant.id == applicant_id).update(update_fields)
    db.commit()
    
    # Refresh the applicant object
    db.refresh(applicant)
    
    # Get program info
    program = db.query(Program).filter(Program.id == applicant.program_id).first()
    
    return ApplicantResponse(
        id=applicant.id,
        applicant_code=applicant.applicant_code,
        full_name=applicant.full_name,
        phone=applicant.phone,
        mailing_address=applicant.mailing_address,
        city=applicant.city,
        state=applicant.state,
        postal_code=applicant.postal_code,
        program_id=applicant.program_id,
        program_name=program.name,
        term=program.term
    )

@app.post("/applicants/{applicant_id}/notes", response_model=ApplicantNoteResponse, operation_id="add_applicant_note")
def add_applicant_note(
    applicant_id: int,
    note_request: ApplicantNoteRequest,
    db: SessionLocal = Depends(get_db)
):
    # For this implementation, we'll assume user_id is 1 (default user)
    # In a real app, this would come from authentication
    user_id = 1
    
    note = ApplicantNote(
        applicant_id=applicant_id,
        user_id=user_id,
        note=note_request.note
    )
    
    db.add(note)
    db.commit()
    db.refresh(note)
    
    return ApplicantNoteResponse(
        id=note.id,
        applicant_id=note.applicant_id,
        user_id=note.user_id,
        note=note.note,
        created_at=note.created_at.isoformat()
    )
