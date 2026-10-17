import os
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


@app.post("/contact-notes", operation_id="create_contact_note_with_followup")
def create_contact_note_with_followup(
    patient_mrn: str = Query(..., description="Medical Record Number of the patient"),
    author_user_code: str = Query(..., description="User code of the person creating the note"),
    context_tag_name: str = Query(..., description="Context tag name for the note"),
    note_type: str = Query(..., description="Type of contact note (e.g., voicemail)"),
    priority: str = Query(..., description="Priority level of the note (Normal/Urgent)"),
    subject: str = Query(..., description="Subject line for the contact note"),
    body: str = Query(..., description="Detailed content of the contact note"),
    occurred_at: str = Query(..., description="Timestamp when the contact occurred (ISO 8601 format)"),
    assigned_to_user_code: str = Query(..., description="User code of the PCP who should receive the follow-up task"),
    followup_subject: str = Query(..., description="Subject line for the generated follow-up task"),
    care_plan_code: str = Query(None, description="Care plan code to associate with the note"),
):
    # Parse occurred_at timestamp - support both T...Z and space-separated formats
    try:
        # Try parsing ISO 8601 with Z suffix
        if occurred_at.endswith('Z'):
            parsed_occurred_at = datetime.fromisoformat(occurred_at.replace('Z', '+00:00'))
        else:
            parsed_occurred_at = datetime.fromisoformat(occurred_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid occurred_at timestamp format")

    # Get current user ID from environment for audit purposes if needed
    current_user_id_env = os.environ.get("AWM_CURRENT_USER_ID")

    with engine.begin() as conn:
        # Look up patient by MRN
        result = conn.execute(text("SELECT id FROM patients WHERE mrn = :mrn"), {"mrn": patient_mrn})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Patient with MRN {patient_mrn} not found")
        patient_id = row[0]

        # Look up author user by user_code
        result = conn.execute(text("SELECT id FROM users WHERE user_code = :user_code"), {"user_code": author_user_code})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Author user with code {author_user_code} not found")
        author_user_id = row[0]

        # Look up context tag by tag_name
        result = conn.execute(text("SELECT id FROM contact_note_context_tags WHERE tag_name = :tag_name"), {"tag_name": context_tag_name})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Context tag '{context_tag_name}' not found")
        context_tag_id = row[0]

        # Look up care plan if provided
        care_plan_id = None
        if care_plan_code:
            result = conn.execute(text("SELECT id FROM care_plans WHERE plan_code = :plan_code AND patient_id = :patient_id"), 
                                  {"plan_code": care_plan_code, "patient_id": patient_id})
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Care plan with code {care_plan_code} not found for patient {patient_mrn}")
            care_plan_id = row[0]

        # Validate note_type against allowed values
        allowed_note_types = ['voicemail', 'phone_call', 'email', 'in_person', 'portal_message', 'other']
        if note_type not in allowed_note_types:
            raise HTTPException(status_code=400, detail=f"Invalid note_type. Must be one of: {allowed_note_types}")

        # Validate priority against allowed values
        allowed_priorities = ['Normal', 'Urgent']
        if priority not in allowed_priorities:
            raise HTTPException(status_code=400, detail=f"Invalid priority. Must be one of: {allowed_priorities}")

        # Insert contact note
        insert_note_sql = """
            INSERT INTO contact_notes (patient_id, author_user_id, care_plan_id, context_tag_id, note_type, priority, subject, body, occurred_at)
            VALUES (:patient_id, :author_user_id, :care_plan_id, :context_tag_id, :note_type, :priority, :subject, :body, :occurred_at)
        """
        result = conn.execute(text(insert_note_sql), {
            "patient_id": patient_id,
            "author_user_id": author_user_id,
            "care_plan_id": care_plan_id,
            "context_tag_id": context_tag_id,
            "note_type": note_type,
            "priority": priority,
            "subject": subject,
            "body": body,
            "occurred_at": parsed_occurred_at.strftime("%Y-%m-%d %H:%M:%S")
        })
        contact_note_id = result.lastrowid

        # Look up assigned_to user by user_code
        result = conn.execute(text("SELECT id FROM users WHERE user_code = :user_code"), {"user_code": assigned_to_user_code})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Assigned-to user with code {assigned_to_user_code} not found")
        assigned_to_user_id = row[0]

        # Calculate due date: within 24 hours from now
        due_datetime = datetime.utcnow() + timedelta(hours=24)
        due_str = due_datetime.strftime("%Y-%m-%d %H:%M:%S")

        # Determine follow-up task priority based on note priority
        followup_priority = priority  # Match the note's priority

        # Insert follow-up task
        insert_task_sql = """
            INSERT INTO follow_up_tasks (contact_note_id, patient_id, assigned_to_user_id, created_by_user_id, subject, status, priority, due_at)
            VALUES (:contact_note_id, :patient_id, :assigned_to_user_id, :created_by_user_id, :subject, :status, :priority, :due_at)
        """
        result = conn.execute(text(insert_task_sql), {
            "contact_note_id": contact_note_id,
            "patient_id": patient_id,
            "assigned_to_user_id": assigned_to_user_id,
            "created_by_user_id": author_user_id,
            "subject": followup_subject,
            "status": "open",
            "priority": followup_priority,
            "due_at": due_str
        })
        follow_up_task_id = result.lastrowid

    return {
        "contact_note_id": contact_note_id,
        "follow_up_task_id": follow_up_task_id
    }
