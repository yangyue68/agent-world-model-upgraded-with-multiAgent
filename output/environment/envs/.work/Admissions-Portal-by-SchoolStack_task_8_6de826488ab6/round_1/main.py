import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# --- Configuration ---
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required.")

# Ensure SQLite URL format for SQLAlchemy
if DATABASE_PATH.startswith("sqlite:///"):
    db_url = DATABASE_PATH
else:
    # Assume it's a file path if not already a URL, though spec says absolute SQLAlchemy SQLite URL
    db_url = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Admissions Portal API", version="1.0.0")

# --- Models ---

class ApplicationResponse(BaseModel):
    id: int
    application_number: str
    applicant_id: int
    intake_id: int
    status: str
    submitted_at: Optional[str] = None

class WithdrawRequest(BaseModel):
    note_text: Optional[str] = None
    reason: Optional[str] = None

class WithdrawResponse(BaseModel):
    success: bool
    new_status: str
    application_id: int

class AddNoteRequest(BaseModel):
    note: str
    is_internal: bool = True

class AddNoteResponse(BaseModel):
    id: int
    created_at: str

class LogEmailRequest(BaseModel):
    recipient_email: str
    subject: str
    body: Optional[str] = None
    template_name: Optional[str] = None

class LogEmailResponse(BaseModel):
    id: int
    status: str

class ApplicantResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str

# --- Helper Functions ---

def get_current_user_id():
    return int(os.environ.get("AWM_CURRENT_USER_ID", "1"))

# --- Endpoints ---

@app.get("/applications/{application_number}", response_model=ApplicationResponse, operation_id="get_application_details")
def get_application_details(application_number: str = Path(...)):
    with SessionLocal() as db:
        result = db.execute(
            text("SELECT id, application_number, applicant_id, intake_id, status, submitted_at FROM applications WHERE application_number = :num"),
            {"num": application_number}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Application not found")
        
        # Handle nullable submitted_at which might be string or datetime depending on driver/config
        sub_at = None
        if result.submitted_at:
            if isinstance(result.submitted_at, datetime):
                sub_at = result.submitted_at.isoformat()
            else:
                sub_at = str(result.submitted_at)
        
        return ApplicationResponse(
            id=result.id,
            application_number=result.application_number,
            applicant_id=result.applicant_id,
            intake_id=result.intake_id,
            status=result.status,
            submitted_at=sub_at
        )

@app.patch("/applications/{application_number}/withdraw", response_model=WithdrawResponse, operation_id="withdraw_application")
def withdraw_application(
    application_number: str = Path(...),
    payload: WithdrawRequest = Body(...)
):
    current_user_id = get_current_user_id()
    
    with SessionLocal() as db:
        # 1. Find application
        app_row = db.execute(
            text("SELECT id, status FROM applications WHERE application_number = :num"),
            {"num": application_number}
        ).fetchone()
        
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found")
        
        app_id = app_row.id
        old_status = app_row.status
        
        if old_status == "Withdrawn":
             raise HTTPException(status_code=409, detail="Application is already withdrawn")

        # 2. Update Status
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            text("UPDATE applications SET status = 'Withdrawn', updated_at = :now WHERE id = :id"),
            {"now": now, "id": app_id}
        )
        
        # 3. Insert History
        reason_text = payload.reason if payload.reason else "Applicant requested withdrawal"
        db.execute(
            text("""INSERT INTO application_status_history 
                    (application_id, previous_status, new_status, changed_by_user_id, changed_at, reason) 
                    VALUES (:app_id, :prev, :new, :user, :time, :reason)"""),
            {
                "app_id": app_id,
                "prev": old_status,
                "new": "Withdrawn",
                "user": current_user_id,
                "time": now,
                "reason": reason_text
            }
        )
        
        # 4. Insert Note if provided
        if payload.note_text:
            db.execute(
                text("""INSERT INTO application_notes 
                        (application_id, author_user_id, note, is_internal, created_at, updated_at) 
                        VALUES (:app_id, :user, :note, 1, :time, :time)"""),
                {
                    "app_id": app_id,
                    "user": current_user_id,
                    "note": payload.note_text,
                    "time": now
                }
            )
            
        db.commit()
        
        return WithdrawResponse(
            success=True,
            new_status="Withdrawn",
            application_id=app_id
        )

@app.post("/applications/{application_number}/notes", response_model=AddNoteResponse, operation_id="add_application_note")
def add_application_note(
    application_number: str = Path(...),
    payload: AddNoteRequest = Body(...)
):
    current_user_id = get_current_user_id()
    
    with SessionLocal() as db:
        app_row = db.execute(
            text("SELECT id FROM applications WHERE application_number = :num"),
            {"num": application_number}
        ).fetchone()
        
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found")
        
        app_id = app_row.id
        now = datetime.now(timezone.utc).isoformat()
        
        result = db.execute(
            text("""INSERT INTO application_notes 
                    (application_id, author_user_id, note, is_internal, created_at, updated_at) 
                    VALUES (:app_id, :user, :note, :is_int, :time, :time)"""),
            {
                "app_id": app_id,
                "user": current_user_id,
                "note": payload.note,
                "is_int": 1 if payload.is_internal else 0,
                "time": now
            }
        )
        db.commit()
        
        # Get the ID of the inserted row
        note_id = result.lastrowid
        
        return AddNoteResponse(
            id=note_id,
            created_at=now
        )

@app.post("/applications/{application_number}/emails/log", response_model=LogEmailResponse, operation_id="log_withdrawal_email")
def log_withdrawal_email(
    application_number: str = Path(...),
    payload: LogEmailRequest = Body(...)
):
    current_user_id = get_current_user_id()
    
    with SessionLocal() as db:
        app_row = db.execute(
            text("SELECT id FROM applications WHERE application_number = :num"),
            {"num": application_number}
        ).fetchone()
        
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found")
        
        app_id = app_row.id
        now = datetime.now(timezone.utc).isoformat()
        
        result = db.execute(
            text("""INSERT INTO email_log 
                    (application_id, recipient_email, subject, body, template_name, sent_by_user_id, sent_at, status, created_at) 
                    VALUES (:app_id, :email, :subj, :body, :tmpl, :user, :time, 'Sent', :time)"""),
            {
                "app_id": app_id,
                "email": payload.recipient_email,
                "subj": payload.subject,
                "body": payload.body,
                "tmpl": payload.template_name,
                "user": current_user_id,
                "time": now
            }
        )
        db.commit()
        
        log_id = result.lastrowid
        
        return LogEmailResponse(
            id=log_id,
            status="Sent"
        )

@app.get("/applicants/{applicant_id}", response_model=ApplicantResponse, operation_id="get_applicant_details")
def get_applicant_details(applicant_id: int = Path(...)):
    with SessionLocal() as db:
        result = db.execute(
            text("SELECT id, first_name, last_name, email FROM applicants WHERE id = :id"),
            {"id": applicant_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Applicant not found")
            
        return ApplicantResponse(
            id=result.id,
            first_name=result.first_name,
            last_name=result.last_name,
            email=result.email
        )
