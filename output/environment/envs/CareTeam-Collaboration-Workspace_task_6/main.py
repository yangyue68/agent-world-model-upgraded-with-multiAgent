import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from contextlib import asynccontextmanager

# Database setup - DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)


class ContactNoteResponse(BaseModel):
    id: int
    patient_id: int
    author_user_id: int
    contact_datetime: str
    duration_minutes: int
    contact_method: str
    transcription_enabled: bool
    note_text: Optional[str] = None
    care_plan_id: Optional[int] = None
    task_id: Optional[int] = None
    created_at: str
    updated_at: str


app = FastAPI(title="CareTeam Collaboration Workspace API")


@app.post("/contact-notes", response_model=ContactNoteResponse, operation_id="create_contact_note")
def create_contact_note(
    patient_mrn: str = Query(..., description="Medical Record Number of the patient"),
    author_user_id: int = Query(..., description="ID of the user creating the note"),
    contact_datetime: str = Query(..., description="Date and time of the contact in ISO format"),
    duration_minutes: int = Query(..., description="Duration of the contact in minutes"),
    contact_method: str = Query(..., description="Method of contact (phone, in-person, video, email, portal, other)"),
    transcription_enabled: bool = Query(..., description="Whether voice-to-text transcription was enabled"),
    note_text: Optional[str] = Query(None, description="Text content of the note"),
    care_plan_identifier: Optional[str] = Query(None, description="Identifier of the care plan to link"),
    care_plan_version: Optional[str] = Query(None, description="Version of the care plan to link"),
    task_identifier: Optional[str] = Query(None, description="Identifier of the task to link"),
    tag_names: Optional[List[str]] = Query(None, description="Names of tags to associate with the note"),
):
    # Validate contact_method against allowed values from schema CHECK constraint
    allowed_methods = ['phone', 'in-person', 'video', 'email', 'portal', 'other']
    if contact_method not in allowed_methods:
        raise HTTPException(status_code=400, detail=f"Invalid contact_method. Must be one of {allowed_methods}")

    # Parse contact_datetime - support both T...Z and space-separated formats
    try:
        # Try parsing as ISO 8601 with Z suffix
        dt_str = contact_datetime.replace('Z', '+00:00')
        parsed_dt = datetime.fromisoformat(dt_str)
        # Normalize to string without timezone info for storage consistency if needed, 
        # but we'll store what's provided normalized or just pass through.
        # The DB stores DATETIME. Let's ensure it's a clean string.
        stored_datetime = parsed_dt.strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        try:
            # Fallback for space separated like '2024-06-12 14:30:00'
            parsed_dt = datetime.strptime(contact_datetime, '%Y-%m-%d %H:%M:%S')
            stored_datetime = parsed_dt.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
             # One more attempt for simple date-time without seconds
            try:
                parsed_dt = datetime.strptime(contact_datetime, '%Y-%m-%dT%H:%M')
                stored_datetime = parsed_dt.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                 try:
                    parsed_dt = datetime.strptime(contact_datetime, '%Y-%m-%d %H:%M')
                    stored_datetime = parsed_dt.strftime('%Y-%m-%d %H:%M:%S')
                 except ValueError:
                     raise HTTPException(status_code=400, detail="Invalid contact_datetime format")

    with engine.connect() as conn:
        # Find patient by MRN
        res = conn.execute(text("SELECT id FROM patients WHERE mrn = :mrn"), {"mrn": patient_mrn})
        row = res.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Patient not found")
        patient_id = row[0]

        # Verify author exists
        res = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": author_user_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Author user not found")

        care_plan_id = None
        if care_plan_identifier and care_plan_version:
            res = conn.execute(
                text("SELECT id FROM care_plans WHERE plan_identifier = :pid AND version = :ver AND patient_id = :ptid"),
                {"pid": care_plan_identifier, "ver": care_plan_version, "ptid": patient_id}
            )
            cp_row = res.fetchone()
            if cp_row:
                care_plan_id = cp_row[0]
            else:
                # If specified but not found, should we error? Spec says optional link. 
                # Usually if provided explicitly, it should exist. But let's assume strictness isn't required unless stated.
                # However, linking to non-existent CP might violate FK if we insert ID. So leave as None if not found.
                pass
        
        task_id = None
        if task_identifier:
            res = conn.execute(
                text("SELECT id FROM tasks WHERE task_identifier = :tid AND patient_id = :ptid"),
                {"tid": task_identifier, "ptid": patient_id}
            )
            t_row = res.fetchone()
            if t_row:
                task_id = t_row[0]

        trans_int = 1 if transcription_enabled else 0

        now_ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        # Insert contact note
        result = conn.execute(
            text("""
                INSERT INTO contact_notes (
                    patient_id, author_user_id, contact_datetime, duration_minutes,
                    contact_method, transcription_enabled, note_text,
                    care_plan_id, task_id, created_at, updated_at
                ) VALUES (
                    :patient_id, :author_user_id, :contact_datetime, :duration_minutes,
                    :contact_method, :transcription_enabled, :note_text,
                    :care_plan_id, :task_id, :created_at, :updated_at
                )
            """),
            {
                "patient_id": patient_id,
                "author_user_id": author_user_id,
                "contact_datetime": stored_datetime,
                "duration_minutes": duration_minutes,
                "contact_method": contact_method,
                "transcription_enabled": trans_int,
                "note_text": note_text,
                "care_plan_id": care_plan_id,
                "task_id": task_id,
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        )
        
        new_note_id = result.lastrowid
        
        # Handle Tags
        if tag_names:
            for tag_name in tag_names:
                # Get tag ID
                res = conn.execute(text("SELECT id FROM tags WHERE name = :name"), {"name": tag_name})
                tag_row = res.fetchone()
                if tag_row:
                    tag_id = tag_row[0]
                    # Link tag to note
                    conn.execute(
                        text("INSERT OR IGNORE INTO contact_note_tags (contact_note_id, tag_id, created_at) VALUES (:cnid, :tid, :ts)"),
                        {"cnid": new_note_id, "tid": tag_id, "ts": now_ts}
                    )
        
        conn.commit()

        # Fetch the created record to return full details including defaults
        res = conn.execute(
            text("""
                SELECT id, patient_id, author_user_id, contact_datetime, duration_minutes,
                       contact_method, transcription_enabled, note_text,
                       care_plan_id, task_id, created_at, updated_at
                FROM contact_notes WHERE id = :id
            """),
            {"id": new_note_id}
        )
        final_row = res.fetchone()
        
        if not final_row:
             raise HTTPException(status_code=500, detail="Failed to retrieve created note")

        return ContactNoteResponse(
            id=final_row[0],
            patient_id=final_row[1],
            author_user_id=final_row[2],
            contact_datetime=str(final_row[3]),
            duration_minutes=final_row[4],
            contact_method=final_row[5],
            transcription_enabled=bool(final_row[6]),
            note_text=final_row[7],
            care_plan_id=final_row[8],
            task_id=final_row[9],
            created_at=str(final_row[10]),
            updated_at=str(final_row[11])
        )
