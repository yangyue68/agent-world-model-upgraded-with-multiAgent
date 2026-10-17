import os
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, func, case
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
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    term = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Applicant(Base):
    __tablename__ = "applicants"
    id = Column(Integer, primary_key=True)
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
    id = Column(Integer, primary_key=True)
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
    phone: Optional[str]
    mailing_address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    postal_code: Optional[str]
    program_id: int
    program_name: str
    term: str

class ApplicantUpdateRequest(BaseModel):
    phone: Optional[str] = None
    mailing_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None

class ApplicantNoteRequest(BaseModel):
    note: str

class ApplicantNoteResponse(BaseModel):
    id: int
    applicant_id: int
    user_id: int
    note: str
    created_at: str

# FastAPI App
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
    program_name: Optional[str] = None,
    term: Optional[str] = None,
    db: Session = Depends(get_db)
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
    update_data: ApplicantUpdateRequest,
    db: Session = Depends(get_db)
):
    applicant = db.query(Applicant).filter(Applicant.id == applicant_id).first()
    
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
        
    # Update fields if provided
    if update_data.phone is not None:
        applicant.phone = update_data.phone
    if update_data.mailing_address is not None:
        applicant.mailing_address = update_data.mailing_address
    if update_data.city is not None:
        applicant.city = update_data.city
    if update_data.state is not None:
        applicant.state = update_data.state
    if update_data.postal_code is not None:
        applicant.postal_code = update_data.postal_code
        
    applicant.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(applicant)
    
    # Get program info for response
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
    db: Session = Depends(get_db)
):
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Verify applicant exists
    applicant = db.query(Applicant).filter(Applicant.id == applicant_id).first()
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
        
    # Verify user exists
    user = db.query(User).filter(User.id == current_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # Create note
    note = ApplicantNote(
        applicant_id=applicant_id,
        user_id=current_user_id,
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
