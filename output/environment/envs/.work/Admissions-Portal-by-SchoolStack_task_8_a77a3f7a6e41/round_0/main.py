import os
import sqlite3
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text, select, and_
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = "sqlite:////tmp/test.db"
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# FastAPI app
app = FastAPI()

# Models
class ApplicationDetailsResponse(BaseModel):
    id: int
    application_number: str
    applicant_id: int
    intake_id: int
    status: str
    submitted_at: Optional[str]

class WithdrawApplicationRequest(BaseModel):
    note_text: Optional[str] = None
    reason: Optional[str] = None

class WithdrawApplicationResponse(BaseModel):
    success: bool
    new_status: str
    application_id: int

class AddApplicationNoteRequest(BaseModel):
    note: str
    is_internal: bool = True

class AddApplicationNoteResponse(BaseModel):
    id: int
    created_at: str

class LogWithdrawalEmailRequest(BaseModel):
    recipient_email: str
    subject: str
    body: Optional[str] = None
    template_name: Optional[str] = None

class LogWithdrawalEmailResponse(BaseModel):
    id: int
    status: str

class ApplicantDetailsResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str

# Helper functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user_id():
    return 1  # From runtime_context

# Endpoints
@app.get("/applications/{application_number}", response_model=ApplicationDetailsResponse)
def get_application_details(application_number: str, db=Depends(get_db)):
    stmt = select(
        text("id"),
        text("application_number"),
        text("applicant_id"),
        text("intake_id"),
        text("status"),
        text("submitted_at")
    ).select_from(text("applications")).where(text("application_number = :app_num"))
    
    result = db.execute(stmt, {"app_num": application_number}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Application not found")
    
    return ApplicationDetailsResponse(
        id=result[0],
        application_number=result[1],
        applicant_id=result[2],
        intake_id=result[3],
        status=result[4],
        submitted_at=result[5]
    )

@app.patch("/applications/{application_number}/withdraw", response_model=WithdrawApplicationResponse)
def withdraw_application(
    application_number: str,
    request: WithdrawApplicationRequest,
    db=Depends(get_db)
):
    # Get current application details
    stmt = select(
        text("id"),
        text("status"),
        text("applicant_id")
    ).select_from(text("applications")).where(text("application_number = :app_num"))
    
    result = db.execute(stmt, {"app_num": application_number}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Application not found")
    
    application_id = result[0]
    current_status = result[1]
    applicant_id = result[2]
    
    # Check if application is already withdrawn
    if current_status == "Withdrawn":
        raise HTTPException(status_code=400, detail="Application is already withdrawn")
    
    # Update application status
    update_stmt = text("""
        UPDATE applications 
        SET status = 'Withdrawn', updated_at = CURRENT_TIMESTAMP 
        WHERE id = :app_id
    """)
    
    db.execute(update_stmt, {"app_id": application_id})
    
    # Record status change in history
    insert_history_stmt = text("""
        INSERT INTO application_status_history 
        (application_id, previous_status, new_status, changed_by_user_id, changed_at, reason)
        VALUES (:app_id, :prev_status, 'Withdrawn', :user_id, CURRENT_TIMESTAMP, :reason)
    """)
    
    db.execute(insert_history_stmt, {
        "app_id": application_id,
        "prev_status": current_status,
        "user_id": get_current_user_id(),
        "reason": request.reason or "Withdrawal initiated by admissions officer"
    })
    
    # Add note if provided
    if request.note_text:
        insert_note_stmt = text("""
            INSERT INTO application_notes 
            (application_id, author_user_id, note, is_internal, created_at, updated_at)
            VALUES (:app_id, :user_id, :note, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        
        db.execute(insert_note_stmt, {
            "app_id": application_id,
            "user_id": get_current_user_id(),
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
    db=Depends(get_db)
):
    # Get application ID
    stmt = select(text("id")).select_from(text("applications")).where(text("application_number = :app_num"))
    result = db.execute(stmt, {"app_num": application_number}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Application not found")
    
    application_id = result[0]
    
    # Insert note
    insert_stmt = text("""
        INSERT INTO application_notes 
        (application_id, author_user_id, note, is_internal, created_at, updated_at)
        VALUES (:app_id, :user_id, :note, :is_internal, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        RETURNING id, created_at
    """)
    
    result = db.execute(insert_stmt, {
        "app_id": application_id,
        "user_id": get_current_user_id(),
        "note": request.note,
        "is_internal": 1 if request.is_internal else 0
    }).fetchone()
    
    db.commit()
    
    return AddApplicationNoteResponse(
        id=result[0],
        created_at=result[1]
    )

@app.post("/applications/{application_number}/emails/log", response_model=LogWithdrawalEmailResponse)
def log_withdrawal_email(
    application_number: str,
    request: LogWithdrawalEmailRequest,
    db=Depends(get_db)
):
    # Get application ID
    stmt = select(text("id")).select_from(text("applications")).where(text("application_number = :app_num"))
    result = db.execute(stmt, {"app_num": application_number}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Application not found")
    
    application_id = result[0]
    
    # Insert email log
    insert_stmt = text("""
        INSERT INTO email_log 
        (application_id, recipient_email, subject, body, template_name, sent_by_user_id, sent_at, status, created_at)
        VALUES (:app_id, :recipient_email, :subject, :body, :template_name, :user_id, CURRENT_TIMESTAMP, 'Sent', CURRENT_TIMESTAMP)
        RETURNING id, status
    """)
    
    result = db.execute(insert_stmt, {
        "app_id": application_id,
        "recipient_email": request.recipient_email,
        "subject": request.subject,
        "body": request.body,
        "template_name": request.template_name,
        "user_id": get_current_user_id()
    }).fetchone()
    
    db.commit()
    
    return LogWithdrawalEmailResponse(
        id=result[0],
        status=result[1]
    )

@app.get("/applicants/{applicant_id}", response_model=ApplicantDetailsResponse)
def get_applicant_details(applicant_id: int, db=Depends(get_db)):
    stmt = select(
        text("id"),
        text("first_name"),
        text("last_name"),
        text("email")
    ).select_from(text("applicants")).where(text("id = :app_id"))
    
    result = db.execute(stmt, {"app_id": applicant_id}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Applicant not found")
    
    return ApplicantDetailsResponse(
        id=result[0],
        first_name=result[1],
        last_name=result[2],
        email=result[3]
    )

# Main execution
if __name__ == "__main__":
    # Initialize database with schema if needed
    pass
