import os
import sqlite3
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, Text, Date, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Database setup
DATABASE_PATH = "sqlite:////tmp/test.db"
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models (using raw SQL for simplicity as per instructions)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI()

# Request/Response Models
class ApplicationWithdrawRequest(BaseModel):
    note_text: Optional[str] = None
    reason: Optional[str] = None

class ApplicationNoteRequest(BaseModel):
    note: str
    is_internal: bool = True

class EmailLogRequest(BaseModel):
    recipient_email: str
    subject: str
    body: Optional[str] = None
    template_name: Optional[str] = None

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

class GetApplicationDetailsResponse(BaseModel):
    id: int
    application_number: str
    applicant_id: int
    intake_id: int
    status: str
    submitted_at: Optional[str]

class GetApplicantDetailsResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str

# Helper functions
def get_current_user_id():
    # In real implementation, this would come from auth context
    return 1

def get_application_by_number(db: Session, application_number: str):
    result = db.execute(text("""
        SELECT id, application_number, applicant_id, intake_id, status, submitted_at
        FROM applications WHERE application_number = :application_number
    """), {"application_number": application_number}).fetchone()
    return result

def get_applicant_details(db: Session, applicant_id: int):
    result = db.execute(text("""
        SELECT id, first_name, last_name, email
        FROM applicants WHERE id = :applicant_id
    """), {"applicant_id": applicant_id}).fetchone()
    return result

def update_application_status(db: Session, application_id: int, new_status: str, reason: Optional[str] = None):
    # Update application status
    db.execute(text("""
        UPDATE applications SET status = :new_status, updated_at = CURRENT_TIMESTAMP
        WHERE id = :application_id
    """), {"new_status": new_status, "application_id": application_id})
    
    # Record in status history
    current_user_id = get_current_user_id()
    db.execute(text("""
        INSERT INTO application_status_history 
        (application_id, previous_status, new_status, changed_by_user_id, reason)
        VALUES (:application_id, (SELECT status FROM applications WHERE id = :application_id), :new_status, :changed_by_user_id, :reason)
    """), {
        "application_id": application_id,
        "new_status": new_status,
        "changed_by_user_id": current_user_id,
        "reason": reason
    })

def add_application_note(db: Session, application_id: int, note_text: str, is_internal: bool = True):
    current_user_id = get_current_user_id()
    result = db.execute(text("""
        INSERT INTO application_notes (application_id, author_user_id, note, is_internal)
        VALUES (:application_id, :author_user_id, :note, :is_internal)
        RETURNING id, created_at
    """), {
        "application_id": application_id,
        "author_user_id": current_user_id,
        "note": note_text,
        "is_internal": 1 if is_internal else 0
    })
    db.commit()
    return result.fetchone()

def log_withdrawal_email(db: Session, application_id: int, recipient_email: str, subject: str, body: Optional[str] = None, template_name: Optional[str] = None):
    current_user_id = get_current_user_id()
    result = db.execute(text("""
        INSERT INTO email_log (application_id, recipient_email, subject, body, template_name, sent_by_user_id)
        VALUES (:application_id, :recipient_email, :subject, :body, :template_name, :sent_by_user_id)
        RETURNING id, status
    """), {
        "application_id": application_id,
        "recipient_email": recipient_email,
        "subject": subject,
        "body": body,
        "template_name": template_name,
        "sent_by_user_id": current_user_id
    })
    db.commit()
    return result.fetchone()

# Endpoints
@app.get("/applications/{application_number}", response_model=GetApplicationDetailsResponse, operation_id="get_application_details")
def get_application_details(application_number: str, db: Session = Depends(get_db)):
    application = get_application_by_number(db, application_number)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return GetApplicationDetailsResponse(
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
    request: ApplicationWithdrawRequest,
    db: Session = Depends(get_db)
):
    application = get_application_by_number(db, application_number)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    if application.status == "Withdrawn":
        raise HTTPException(status_code=400, detail="Application is already withdrawn")
    
    # Update status to Withdrawn
    update_application_status(db, application.id, "Withdrawn", request.reason)
    
    # Add note if provided
    if request.note_text:
        add_application_note(db, application.id, request.note_text, is_internal=True)
    
    return WithdrawApplicationResponse(
        success=True,
        new_status="Withdrawn",
        application_id=application.id
    )

@app.post("/applications/{application_number}/notes", response_model=AddApplicationNoteResponse, operation_id="add_application_note")
def add_application_note_endpoint(
    application_number: str,
    request: ApplicationNoteRequest,
    db: Session = Depends(get_db)
):
    application = get_application_by_number(db, application_number)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    note = add_application_note(db, application.id, request.note, request.is_internal)
    return AddApplicationNoteResponse(
        id=note.id,
        created_at=note.created_at.isoformat() if note.created_at else None
    )

@app.post("/applications/{application_number}/emails/log", response_model=LogWithdrawalEmailResponse, operation_id="log_withdrawal_email")
def log_withdrawal_email_endpoint(
    application_number: str,
    request: EmailLogRequest,
    db: Session = Depends(get_db)
):
    application = get_application_by_number(db, application_number)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    email_log = log_withdrawal_email(db, application.id, request.recipient_email, request.subject, request.body, request.template_name)
    return LogWithdrawalEmailResponse(
        id=email_log.id,
        status=email_log.status
    )

@app.get("/applicants/{applicant_id}", response_model=GetApplicantDetailsResponse, operation_id="get_applicant_details")
def get_applicant_details_endpoint(applicant_id: int, db: Session = Depends(get_db)):
    applicant = get_applicant_details(db, applicant_id)
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")
    return GetApplicantDetailsResponse(
        id=applicant.id,
        first_name=applicant.first_name,
        last_name=applicant.last_name,
        email=applicant.email
    )
