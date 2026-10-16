import os
import sqlite3
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text, select, and_
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_URL", "sqlite:////tmp/test.db")
if DATABASE_PATH.startswith("sqlite:///"):
    DB_FILE_PATH = DATABASE_PATH[10:]
else:
    raise ValueError("Invalid database URL")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Models
class Application(BaseModel):
    id: int
    application_number: str
    applicant_id: int
    intake_id: int
    status: str
    submitted_at: Optional[str]

class Applicant(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str

class ApplicationNote(BaseModel):
    id: int
    application_id: int
    author_user_id: int
    note: str
    is_internal: bool
    created_at: str

class EmailLog(BaseModel):
    id: int
    application_id: Optional[int]
    recipient_email: str
    subject: str
    body: Optional[str]
    template_name: Optional[str]
    sent_by_user_id: Optional[int]
    sent_at: str
    status: str
    error_message: Optional[str]
    created_at: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Helper functions
def get_current_user_id():
    return 1  # From runtime_context

def get_application_by_number(db, application_number: str):
    stmt = select(text("id, application_number, applicant_id, intake_id, status, submitted_at")).select_from(text("applications")).where(text("application_number = :number"))
    result = db.execute(stmt, {"number": application_number})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return Application(
        id=row[0],
        application_number=row[1],
        applicant_id=row[2],
        intake_id=row[3],
        status=row[4],
        submitted_at=row[5]
    )

def get_applicant_by_id(db, applicant_id: int):
    stmt = select(text("id, first_name, last_name, email")).select_from(text("applicants")).where(text("id = :id"))
    result = db.execute(stmt, {"id": applicant_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Applicant not found")
    return Applicant(
        id=row[0],
        first_name=row[1],
        last_name=row[2],
        email=row[3]
    )

def get_application_status_history(db, application_id: int):
    stmt = select(text("id, application_id, previous_status, new_status, changed_by_user_id, changed_at, reason")).select_from(text("application_status_history")).where(text("application_id = :id")).order_by(text("changed_at DESC"))
    result = db.execute(stmt, {"id": application_id})
    return result.fetchall()

def insert_application_status_history(db, application_id: int, previous_status: str, new_status: str, changed_by_user_id: int, reason: Optional[str] = None):
    stmt = text("""
        INSERT INTO application_status_history (application_id, previous_status, new_status, changed_by_user_id, reason)
        VALUES (:application_id, :previous_status, :new_status, :changed_by_user_id, :reason)
    """)
    db.execute(stmt, {
        "application_id": application_id,
        "previous_status": previous_status,
        "new_status": new_status,
        "changed_by_user_id": changed_by_user_id,
        "reason": reason
    })

def update_application_status(db, application_id: int, new_status: str):
    stmt = text("""
        UPDATE applications 
        SET status = :status, updated_at = CURRENT_TIMESTAMP 
        WHERE id = :id
    """)
    db.execute(stmt, {"status": new_status, "id": application_id})

def insert_application_note(db, application_id: int, author_user_id: int, note: str, is_internal: bool = True):
    stmt = text("""
        INSERT INTO application_notes (application_id, author_user_id, note, is_internal, created_at, updated_at)
        VALUES (:application_id, :author_user_id, :note, :is_internal, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """)
    db.execute(stmt, {
        "application_id": application_id,
        "author_user_id": author_user_id,
        "note": note,
        "is_internal": 1 if is_internal else 0
    })

def insert_email_log(db, application_id: int, recipient_email: str, subject: str, body: Optional[str] = None, template_name: Optional[str] = None, sent_by_user_id: Optional[int] = None):
    stmt = text("""
        INSERT INTO email_log (application_id, recipient_email, subject, body, template_name, sent_by_user_id, sent_at, status, created_at)
        VALUES (:application_id, :recipient_email, :subject, :body, :template_name, :sent_by_user_id, CURRENT_TIMESTAMP, 'Sent', CURRENT_TIMESTAMP)
    """)
    db.execute(stmt, {
        "application_id": application_id,
        "recipient_email": recipient_email,
        "subject": subject,
        "body": body,
        "template_name": template_name,
        "sent_by_user_id": sent_by_user_id
    })

# Endpoints
@app.get("/applications/{application_number}", response_model=Application)
def get_application_details(application_number: str, db=Depends(get_db)):
    return get_application_by_number(db, application_number)

@app.get("/applicants/{applicant_id}", response_model=Applicant)
def get_applicant_details(applicant_id: int, db=Depends(get_db)):
    return get_applicant_by_id(db, applicant_id)

@app.patch("/applications/{application_number}/withdraw")
def withdraw_application(application_number: str, note_text: Optional[str] = None, reason: Optional[str] = None, db=Depends(get_db)):
    # Get application details
    application = get_application_by_number(db, application_number)
    
    # Check if application is already withdrawn
    if application.status == "Withdrawn":
        raise HTTPException(status_code=400, detail="Application is already withdrawn")
    
    # Get current user ID
    current_user_id = get_current_user_id()
    
    # Record status change in history
    insert_application_status_history(db, application.id, application.status, "Withdrawn", current_user_id, reason)
    
    # Update application status
    update_application_status(db, application.id, "Withdrawn")
    
    # Add note if provided
    if note_text:
        insert_application_note(db, application.id, current_user_id, note_text)
    
    # Return success response
    return {
        "success": True,
        "new_status": "Withdrawn",
        "application_id": application.id
    }

@app.post("/applications/{application_number}/notes")
def add_application_note(application_number: str, note: str, is_internal: bool = True, db=Depends(get_db)):
    # Get application details
    application = get_application_by_number(db, application_number)
    
    # Get current user ID
    current_user_id = get_current_user_id()
    
    # Insert note
    insert_application_note(db, application.id, current_user_id, note, is_internal)
    
    # Return note info
    stmt = select(text("id, created_at")).select_from(text("application_notes")).where(text("application_id = :app_id AND author_user_id = :user_id AND note = :note")).order_by(text("created_at DESC")).limit(1)
    result = db.execute(stmt, {"app_id": application.id, "user_id": current_user_id, "note": note})
    row = result.fetchone()
    
    return {
        "id": row[0],
        "created_at": row[1]
    }

@app.post("/applications/{application_number}/emails/log")
def log_withdrawal_email(application_number: str, recipient_email: str, subject: str, body: Optional[str] = None, template_name: Optional[str] = None, db=Depends(get_db)):
    # Get application details
    application = get_application_by_number(db, application_number)
    
    # Get current user ID
    current_user_id = get_current_user_id()
    
    # Insert email log
    insert_email_log(db, application.id, recipient_email, subject, body, template_name, current_user_id)
    
    # Return email log info
    stmt = select(text("id, status")).select_from(text("email_log")).where(text("application_id = :app_id AND recipient_email = :email AND subject = :subject")).order_by(text("sent_at DESC")).limit(1)
    result = db.execute(stmt, {"app_id": application.id, "email": recipient_email, "subject": subject})
    row = result.fetchone()
    
    return {
        "id": row[0],
        "status": row[1]
    }
