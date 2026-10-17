import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CareTeam Collaboration Workspace API")


class ContactNoteResponse(BaseModel):
    id: int
    patient_id: int
    author_user_id: int
    assigned_user_id: Optional[int]
    document_id: Optional[int]
    note_type: str
    visit_date: str
    raw_transcript: str
    created_at: str
    updated_at: str


@app.post("/contact_notes", response_model=ContactNoteResponse, operation_id="create_contact_note")
def create_contact_note(
    patient_mrn: str = Query(..., description="Medical Record Number of the patient"),
    visit_date: str = Query(..., description="Date of the visit in YYYY-MM-DD format"),
    raw_transcript: str = Query(..., description="Raw transcript text of the contact note"),
    assigned_user_code: str = Query(..., description="User code of the social worker to whom the note is assigned"),
    document_code: str = Query(..., description="Document code associated with the contact note"),
):
    db = SessionLocal()
    try:
        # 1. Resolve Patient ID from MRN
        stmt_patient = text("SELECT id FROM patients WHERE mrn = :mrn")
        res_patient = db.execute(stmt_patient, {"mrn": patient_mrn}).fetchone()
        if not res_patient:
            raise HTTPException(status_code=404, detail=f"Patient with MRN {patient_mrn} not found.")
        patient_id = res_patient[0]

        # 2. Resolve Assigned User ID from User Code
        stmt_assigned_user = text("SELECT id FROM users WHERE user_code = :user_code")
        res_assigned_user = db.execute(stmt_assigned_user, {"user_code": assigned_user_code}).fetchone()
        if not res_assigned_user:
            raise HTTPException(status_code=404, detail=f"Assigned user with code {assigned_user_code} not found.")
        assigned_user_id = res_assigned_user[0]

        # 3. Resolve Document ID from Document Code
        stmt_document = text("SELECT id FROM documents WHERE document_code = :doc_code")
        res_document = db.execute(stmt_document, {"doc_code": document_code}).fetchone()
        if not res_document:
            raise HTTPException(status_code=404, detail=f"Document with code {document_code} not found.")
        document_id = res_document[0]

        # 4. Determine Author User ID from Environment Variable (Simulated Logged-in Actor)
        current_user_env = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_env:
             # Fallback or error depending on strictness. Spec says "injects... as env var". 
             # If missing, we can't proceed safely without a default, but usually this implies it exists.
             # Let's assume it exists for valid requests. If not, maybe 401? But spec doesn't mention auth errors explicitly other than business logic.
             # However, looking at previous notes, author_user_id is required.
             raise HTTPException(status_code=500, detail="Current user context not available.")
        
        try:
            author_user_id = int(current_user_env)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid current user ID format.")

        # Verify author exists? Usually good practice, though FK constraint will catch it.
        # The prompt says "database is already populated", so we trust IDs exist if they come from valid sources.
        # But since AWM_CURRENT_USER_ID is external, let's verify it exists to give better error messages if needed, 
        # or just rely on DB integrity. Given constraints, relying on DB is safer/faster unless specific validation is requested.
        # I'll skip explicit check for author existence to keep it simple, assuming valid injection.

        # 5. Insert Contact Note
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Note Type: The task mentions "in-home visit". Looking at existing data, types are 'phone_call', 'clinic_visit'.
        # The DDL does not have a CHECK constraint on note_type listed in the provided schema snippet, 
        # but typically these are enums. The task says "indicating an in-home visit occurred".
        # Common values might be 'home_visit' or similar. Since no enum list is provided in DDL for note_type,
        # and the example policy is illustrative, I should use a reasonable string derived from the task description.
        # Task: "submit a contact note ... indicating an in-home visit".
        # I will use 'home_visit' as the note_type.
        note_type = "home_visit"

        stmt_insert = text("""
            INSERT INTO contact_notes (
                patient_id, 
                author_user_id, 
                assigned_user_id, 
                document_id, 
                note_type, 
                visit_date, 
                raw_transcript, 
                created_at, 
                updated_at
            ) VALUES (
                :patient_id, 
                :author_user_id, 
                :assigned_user_id, 
                :document_id, 
                :note_type, 
                :visit_date, 
                :raw_transcript, 
                :now, 
                :now
            )
        """)
        
        params = {
            "patient_id": patient_id,
            "author_user_id": author_user_id,
            "assigned_user_id": assigned_user_id,
            "document_id": document_id,
            "note_type": note_type,
            "visit_date": visit_date,
            "raw_transcript": raw_transcript,
            "now": now_str
        }
        
        result = db.execute(stmt_insert, params)
        db.commit()
        
        new_id = result.lastrowid
        
        # 6. Fetch the created record to return full details including server-generated defaults if any
        # Although we inserted everything, fetching ensures consistency with DB state (e.g. triggers if any, though none mentioned)
        stmt_select = text("""
            SELECT id, patient_id, author_user_id, assigned_user_id, document_id, note_type, visit_date, raw_transcript, created_at, updated_at
            FROM contact_notes
            WHERE id = :id
        """)
        row = db.execute(stmt_select, {"id": new_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created contact note.")
            
        return ContactNoteResponse(
            id=row[0],
            patient_id=row[1],
            author_user_id=row[2],
            assigned_user_id=row[3],
            document_id=row[4],
            note_type=row[5],
            visit_date=str(row[6]),
            raw_transcript=row[7],
            created_at=str(row[8]),
            updated_at=str(row[9])
        )

    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
