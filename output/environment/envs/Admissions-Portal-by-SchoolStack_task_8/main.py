import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    role = Column(String, nullable=False, default="staff")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    degree_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Intake(Base):
    __tablename__ = "intakes"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    term = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    start_date = Column(String)
    end_date = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Applicant(Base):
    __tablename__ = "applicants"
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    date_of_birth = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True, index=True)
    application_number = Column(String, unique=True, nullable=False)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=False)
    intake_id = Column(Integer, ForeignKey("intakes.id"), nullable=False)
    status = Column(String, nullable=False, default="Submitted")
    submitted_at = Column(DateTime)
    decision_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('Submitted', 'Under Review', 'Interview', 'Decision Pending', 'Admitted', 'Denied', 'Waitlisted', 'Withdrawn')"),
    )

class ApplicationNote(Base):
    __tablename__ = "application_notes"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    author_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    note = Column(Text, nullable=False)
    is_internal = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    document_type = Column(String, nullable=False)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    mime_type = Column(String)
    file_size_bytes = Column(Integer)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"))
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("document_type IN ('Transcript', 'Recommendation Letter', 'Personal Statement', 'Resume', 'Test Score', 'Other')"),
        CheckConstraint("is_active IN (0, 1)"),
    )

class EmailLog(Base):
    __tablename__ = "email_log"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"))
    recipient_email = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text)
    template_name = Column(String)
    sent_by_user_id = Column(Integer, ForeignKey("users.id"))
    sent_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, nullable=False, default="Sent")
    error_message = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('Sent', 'Failed', 'Queued')"),
    )

class ApplicationStatusHistory(Base):
    __tablename__ = "application_status_history"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    previous_status = Column(String)
    new_status = Column(String, nullable=False)
    changed_by_user_id = Column(Integer, ForeignKey("users.id"))
    changed_at = Column(DateTime, default=datetime.utcnow)
    reason = Column(String)

# Pydantic models
class ApplicationDetailsResponse(BaseModel):
    id: int
    application_number: str
    applicant_id: int
    intake_id: int
    status: str
    submitted_at: Optional[str]

class WithdrawApplicationResponse(BaseModel):
    success: bool
    new_status: str
    application_id: int

class AddApplicationNoteResponse(BaseModel):
    id: int
    created_at: str

class LogWithdrawalEmailResponse(BaseModel):
    id: int
    status: str

class ApplicantDetailsResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str

class AddApplicationNoteRequest(BaseModel):
    note: str
    is_internal: bool = True

class LogWithdrawalEmailRequest(BaseModel):
    recipient_email: str
    subject: str
    body: Optional[str] = None
    template_name: Optional[str] = None

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/applications/{application_number}", response_model=ApplicationDetailsResponse, operation_id="get_application_details")
def get_application_details(application_number: str, db: Session = Depends(get_db)):
    application = db.query(Application).filter(Application.application_number == application_number).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    return ApplicationDetailsResponse(
        id=application.id,
        application_number=application.application_number,
        applicant_id=application.applicant_id,
        intake_id=application.intake_id,
        status=application.status,
        submitted_at=application.submitted_at.isoformat() if application.submitted_at else None
    )

@app.patch("/applications/{application_number}/withdraw", response_model=WithdrawApplicationResponse, operation_id="withdraw_application")
def withdraw_application(
    application_number: str,
    note_text: Optional[str] = None,
    reason: Optional[str] = None,
    db: Session = Depends(get_db)
):
    # Get the application
    application = db.query(Application).filter(Application.application_number == application_number).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Record status change in history
    status_history = ApplicationStatusHistory(
        application_id=application.id,
        previous_status=application.status,
        new_status="Withdrawn",
        changed_by_user_id=current_user_id,
        reason=reason
    )
    db.add(status_history)
    
    # Update application status
    application.status = "Withdrawn"
    application.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(application)
    
    # Add note if provided
    if note_text:
        note = ApplicationNote(
            application_id=application.id,
            author_user_id=current_user_id,
            note=note_text,
            is_internal=1
        )
        db.add(note)
        db.commit()
        db.refresh(note)
    
    return WithdrawApplicationResponse(
        success=True,
        new_status="Withdrawn",
        application_id=application.id
    )

@app.post("/applications/{application_number}/notes", response_model=AddApplicationNoteResponse, operation_id="add_application_note")
def add_application_note(
    application_number: str,
    note_data: AddApplicationNoteRequest,
    db: Session = Depends(get_db)
):
    # Get the application
    application = db.query(Application).filter(Application.application_number == application_number).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Create note
    application_note = ApplicationNote(
        application_id=application.id,
        author_user_id=current_user_id,
        note=note_data.note,
        is_internal=int(note_data.is_internal)
    )
    db.add(application_note)
    db.commit()
    db.refresh(application_note)
    
    return AddApplicationNoteResponse(
        id=application_note.id,
        created_at=application_note.created_at.isoformat()
    )

@app.post("/applications/{application_number}/emails/log", response_model=LogWithdrawalEmailResponse, operation_id="log_withdrawal_email")
def log_withdrawal_email(
    application_number: str,
    email_data: LogWithdrawalEmailRequest,
    db: Session = Depends(get_db)
):
    # Get the application
    application = db.query(Application).filter(Application.application_number == application_number).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    # Get current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Create email log entry
    email_log = EmailLog(
        application_id=application.id,
        recipient_email=email_data.recipient_email,
        subject=email_data.subject,
        body=email_data.body,
        template_name=email_data.template_name,
        sent_by_user_id=current_user_id,
        status="Sent"
    )
    db.add(email_log)
    db.commit()
    db.refresh(email_log)
    
    return LogWithdrawalEmailResponse(
        id=email_log.id,
        status=email_log.status
    )

@app.get("/applicants/{applicant_id}", response_model=ApplicantDetailsResponse, operation_id="get_applicant_details")
def get_applicant_details(applicant_id: int, db: Session = Depends(get_db)):
    applicant = db.query(Applicant).filter(Applicant.id == applicant_id).first()
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
    
    return ApplicantDetailsResponse(
        id=applicant.id,
        first_name=applicant.first_name,
        last_name=applicant.last_name,
        email=applicant.email
    )
