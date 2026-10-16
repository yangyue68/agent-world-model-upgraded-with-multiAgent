import os
import sqlite3
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, TEXT, DATE, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Database setup
DATABASE_PATH = "sqlite:////tmp/test.db"
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Runtime context
AWM_CURRENT_USER_ID = 1

# Models (using raw SQL instead of ORM for simplicity)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI()

# Request and Response Models
class WithdrawApplicationRequest(BaseModel):
    note_text: Optional[str] = None
    reason: Optional[str] = None

class AddApplicationNoteRequest(BaseModel):
    note: str
    is_internal: bool = True

class LogWithdrawalEmailRequest(BaseModel):
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

# Helper functions
def get_application_by_number(db: Session, application_number: str):
    result = db.execute(text("""
        SELECT id, application_number, applicant_id, intake_id, status, submitted_at
        FROM applications
        WHERE application_number = :application_number
    """), {"application_number": application_number}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Application not found")
    
    return result

def get_applicant_by_id(db: Session, applicant_id: int):
    result = db.execute(text("""
        SELECT id, first_name, last_name, email
        FROM applicants
        WHERE id = :applicant_id
    """), {"applicant_id": applicant_id}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Applicant not found")
    
    return result

def get_intake_by_id(db: Session, intake_id: int):
    result = db.execute(text("""
        SELECT id, program_id, term, year
        FROM intakes
        WHERE id = :intake_id
    """), {"intake_id": intake_id}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Intake not found")
    
    return result

def get_program_by_id(db: Session, program_id: int):
    result = db.execute(text("""
        SELECT id, name, degree_type
        FROM programs
        WHERE id = :program_id
    """), {"program_id": program_id}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Program not found")
    
    return result

def get_user_by_id(db: Session, user_id: int):
    result = db.execute(text("""
        SELECT id, full_name, email, role
        FROM users
        WHERE id = :user_id
    """), {"user_id": user_id}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    
    return result

# Endpoints
@app.get("/applications/{application_number}", response_model=dict)
def get_application_details(application_number: str, db: Session = Depends(get_db)):
    """Get application details by number"""
    app_record = get_application_by_number(db, application_number)
    return {
        "id": app_record[0],
        "application_number": app_record[1],
        "applicant_id": app_record[2],
        "intake_id": app_record[3],
        "status": app_record[4],
        "submitted_at": app_record[5]
    }

@app.get("/applicants/{applicant_id}", response_model=dict)
def get_applicant_details(applicant_id: int, db: Session = Depends(get_db)):
    """Get applicant details"""
    applicant = get_applicant_by_id(db, applicant_id)
    return {
        "id": applicant[0],
        "first_name": applicant[1],
        "last_name": applicant[2],
        "email": applicant[3]
    }

@app.patch("/applications/{application_number}/withdraw", response_model=WithdrawApplicationResponse)
def withdraw_application(
    application_number: str,
    request: WithdrawApplicationRequest,
    db: Session = Depends(get_db)
):
    """Withdraw an application"""
    # Get the application
    app_record = get_application_by_number(db, application_number)
    application_id = app_record[0]
    current_status = app_record[4]
    
    # Check if already withdrawn
    if current_status == "Withdrawn":
        raise HTTPException(status_code=400, detail="Application is already withdrawn")
    
    # Update application status
    db.execute(text("""
        UPDATE applications
        SET status = 'Withdrawn', updated_at = CURRENT_TIMESTAMP
        WHERE id = :application_id
    """), {"application_id": application_id})
    
    # Record status change in history
    db.execute(text("""
        INSERT INTO application_status_history 
        (application_id, previous_status, new_status, changed_by_user_id, changed_at, reason)
        VALUES (:application_id, :previous_status, 'Withdrawn', :changed_by_user_id, CURRENT_TIMESTAMP, :reason)
    """), {
        "application_id": application_id,
        "previous_status": current_status,
        "changed_by_user_id": AWM_CURRENT_USER_ID,
        "reason": request.reason or "Withdrawal initiated by staff"
    })
    
    # Add note if provided
    if request.note_text:
        db.execute(text("""
            INSERT INTO application_notes 
            (application_id, author_user_id, note, is_internal, created_at, updated_at)
            VALUES (:application_id, :author_user_id, :note, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {
            "application_id": application_id,
            "author_user_id": AWM_CURRENT_USER_ID,
            "note": request.note_text
        })
    
    db.commit()
    
    return WithdrawApplicationResponse(
        success=True,
        new_status="Withdrawn",
        application_id=application_id
    )

@app.post("/applications/{application_number}/notes", response_model=AddApplicationNoteResponse)
def add_application_note(
    application_number: str,
    request: AddApplicationNoteRequest,
    db: Session = Depends(get_db)
):
    """Add an internal note to an application"""
    # Get the application
    app_record = get_application_by_number(db, application_number)
    application_id = app_record[0]
    
    # Insert note
    result = db.execute(text("""
        INSERT INTO application_notes 
        (application_id, author_user_id, note, is_internal, created_at, updated_at)
        VALUES (:application_id, :author_user_id, :note, :is_internal, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        RETURNING id, created_at
    """), {
        "application_id": application_id,
        "author_user_id": AWM_CURRENT_USER_ID,
        "note": request.note,
        "is_internal": 1 if request.is_internal else 0
    })
    
    db.commit()
    
    note_row = result.fetchone()
    return AddApplicationNoteResponse(
        id=note_row[0],
        created_at=note_row[1]
    )

@app.post("/applications/{application_number}/emails/log", response_model=LogWithdrawalEmailResponse)
def log_withdrawal_email(
    application_number: str,
    request: LogWithdrawalEmailRequest,
    db: Session = Depends(get_db)
):
    """Log a withdrawal confirmation email"""
    # Get the application
    app_record = get_application_by_number(db, application_number)
    application_id = app_record[0]
    
    # Insert email log
    result = db.execute(text("""
        INSERT INTO email_log 
        (application_id, recipient_email, subject, body, template_name, sent_by_user_id, sent_at, status, created_at)
        VALUES (:application_id, :recipient_email, :subject, :body, :template_name, :sent_by_user_id, CURRENT_TIMESTAMP, 'Sent', CURRENT_TIMESTAMP)
        RETURNING id, status
    """), {
        "application_id": application_id,
        "recipient_email": request.recipient_email,
        "subject": request.subject,
        "body": request.body,
        "template_name": request.template_name,
        "sent_by_user_id": AWM_CURRENT_USER_ID
    })
    
    db.commit()
    
    email_row = result.fetchone()
    return LogWithdrawalEmailResponse(
        id=email_row[0],
        status=email_row[1]
    )
