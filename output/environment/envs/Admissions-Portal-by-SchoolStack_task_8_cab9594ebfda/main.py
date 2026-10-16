import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

# Strip sqlite:/// prefix to get file path for direct sqlite3 usage if needed, 
# but we will use SQLAlchemy engine which handles the URL.
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Admissions Portal API")

# --- Pydantic Models ---

class ApplicationDetails(BaseModel):
    id: int
    application_number: str
    applicant_id: int
    intake_id: int
    status: str
    submitted_at: Optional[str] = None

class ApplicantDetails(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str

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

# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user_id():
    # From runtime context description: current_user_id is 1
    return 1

# --- Endpoints ---

@app.get("/applications/{application_number}", response_model=ApplicationDetails, operation_id="get_application_details", tags=["Applications"])
def get_application_details(application_number: str = Path(...)):
    db = next(get_db())
    try:
        result = db.execute(
            text("SELECT id, application_number, applicant_id, intake_id, status, submitted_at FROM applications WHERE application_number = :num"),
            {"num": application_number}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Application not found")
        
        # Handle datetime serialization safely
        submitted_at_val = result.submitted_at
        if submitted_at_val is not None:
            if isinstance(submitted_at_val, datetime):
                submitted_at_str = submitted_at_val.isoformat()
            else:
                # If it's already a string (e.g., from SQLite driver returning TEXT), keep as is or normalize
                submitted_at_str = str(submitted_at_val)
        else:
            submitted_at_str = None
        
        return ApplicationDetails(
            id=result.id,
            application_number=result.application_number,
            applicant_id=result.applicant_id,
            intake_id=result.intake_id,
            status=result.status,
            submitted_at=submitted_at_str
        )
    finally:
        db.close()

@app.patch("/applications/{application_number}/withdraw", response_model=WithdrawResponse, operation_id="withdraw_application", tags=["Applications"])
def withdraw_application(
    application_number: str = Path(...),
    payload: WithdrawRequest = Body(...)
):
    db = next(get_db())
    try:
        # 1. Find application
        app_row = db.execute(
            text("SELECT id, status FROM applications WHERE application_number = :num"),
            {"num": application_number}
        ).fetchone()
        
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found")
        
        app_id = app_row.id
        current_status = app_row.status
        
        if current_status == "Withdrawn":
             # Idempotent or conflict? Spec says change status to Withdrawn. 
             # If already withdrawn, maybe just return success? 
             # But usually state transitions are strict. Let's allow it if already withdrawn or treat as no-op success.
             pass

        new_status = "Withdrawn"
        current_user_id = get_current_user_id()
        now = datetime.now(timezone.utc).isoformat()

        # 2. Update Application Status
        db.execute(
            text("UPDATE applications SET status = :status, updated_at = :now WHERE id = :id"),
            {"status": new_status, "now": now, "id": app_id}
        )

        # 3. Record in History
        db.execute(
            text("""
                INSERT INTO application_status_history 
                (application_id, previous_status, new_status, changed_by_user_id, changed_at, reason)
                VALUES (:app_id, :prev, :new, :user_id, :time, :reason)
            """),
            {
                "app_id": app_id,
                "prev": current_status,
                "new": new_status,
                "user_id": current_user_id,
                "time": now,
                "reason": payload.reason
            }
        )

        # 4. Add Note if provided
        if payload.note_text:
            db.execute(
                text("""
                    INSERT INTO application_notes 
                    (application_id, author_user_id, note, is_internal, created_at, updated_at)
                    VALUES (:app_id, :author, :note, 1, :time, :time)
                """),
                {
                    "app_id": app_id,
                    "author": current_user_id,
                    "note": payload.note_text,
                    "time": now
                }
            )

        db.commit()
        
        return WithdrawResponse(
            success=True,
            new_status=new_status,
            application_id=app_id
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/applications/{application_number}/notes", response_model=AddNoteResponse, operation_id="add_application_note", tags=["Applications"])
def add_application_note(
    application_number: str = Path(...),
    payload: AddNoteRequest = Body(...)
):
    db = next(get_db())
    try:
        # Find application ID
        app_row = db.execute(
            text("SELECT id FROM applications WHERE application_number = :num"),
            {"num": application_number}
        ).fetchone()
        
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found")
        
        app_id = app_row.id
        current_user_id = get_current_user_id()
        now = datetime.now(timezone.utc).isoformat()
        
        # Insert Note
        result = db.execute(
            text("""
                INSERT INTO application_notes 
                (application_id, author_user_id, note, is_internal, created_at, updated_at)
                VALUES (:app_id, :author, :note, :is_internal, :time, :time)
            """),
            {
                "app_id": app_id,
                "author": current_user_id,
                "note": payload.note,
                "is_internal": 1 if payload.is_internal else 0,
                "time": now
            }
        )
        db.commit()
        
        # Get generated ID
        note_id = result.lastrowid
        
        return AddNoteResponse(
            id=note_id,
            created_at=now
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/applicants/{applicant_id}", response_model=ApplicantDetails, operation_id="get_applicant_details", tags=["Applications"])
def get_applicant_details(applicant_id: int = Path(...)):
    db = next(get_db())
    try:
        result = db.execute(
            text("SELECT id, first_name, last_name, email FROM applicants WHERE id = :id"),
            {"id": applicant_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Applicant not found")
            
        return ApplicantDetails(
            id=result.id,
            first_name=result.first_name,
            last_name=result.last_name,
            email=result.email
        )
    finally:
        db.close()

@app.post("/applications/{application_number}/emails/log", response_model=LogEmailResponse, operation_id="log_withdrawal_email", tags=["Applications"])
def log_withdrawal_email(
    application_number: str = Path(...),
    payload: LogEmailRequest = Body(...)
):
    db = next(get_db())
    try:
        # Find application ID
        app_row = db.execute(
            text("SELECT id FROM applications WHERE application_number = :num"),
            {"num": application_number}
        ).fetchone()
        
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found")
        
        app_id = app_row.id
        current_user_id = get_current_user_id()
        now = datetime.now(timezone.utc).isoformat()
        
        # Insert Email Log
        result = db.execute(
            text("""
                INSERT INTO email_log 
                (application_id, recipient_email, subject, body, template_name, sent_by_user_id, sent_at, status, created_at)
                VALUES (:app_id, :recipient, :subject, :body, :template, :user_id, :time, 'Sent', :time)
            """),
            {
                "app_id": app_id,
                "recipient": payload.recipient_email,
                "subject": payload.subject,
                "body": payload.body,
                "template": payload.template_name,
                "user_id": current_user_id,
                "time": now
            }
        )
        db.commit()
        
        email_id = result.lastrowid
        
        return LogEmailResponse(
            id=email_id,
            status="Sent"
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
