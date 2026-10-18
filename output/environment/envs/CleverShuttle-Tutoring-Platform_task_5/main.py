import os
from datetime import datetime, timezone as dt_timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CleverShuttle Tutoring Platform API")

# Pydantic Models for Responses
class SessionSearchResponse(BaseModel):
    id: int
    student_id: int
    tutor_id: int
    subject_id: int
    start_time: str
    end_time: str
    timezone: str
    status: str
    notes: Optional[str] = None

class RescheduleRequest(BaseModel):
    new_start_time: str
    new_end_time: str
    notes: Optional[str] = None

class RescheduleResponse(BaseModel):
    session_id: int
    status: str
    reschedule_id: int


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 string to naive datetime (assuming local time context of DB)."""
    # Handle 'Z' suffix and space separators
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    try:
        # Try parsing with timezone info first
        dt = datetime.fromisoformat(dt_str)
        # Convert to UTC then strip tzinfo to store as naive UTC-like or just keep consistent?
        # The DB stores strings like "2024-06-14 17:00:00". 
        # We will normalize to a format compatible with SQLite DATETIME.
        if dt.tzinfo is not None:
            dt = dt.astimezone(dt_timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        # Fallback for simple formats without T/Z
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")


def format_db_datetime(dt: datetime) -> str:
    """Format datetime object to string compatible with SQLite storage."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@app.get("/sessions/search", response_model=List[SessionSearchResponse], operation_id="search_sessions")
def search_sessions(
    tutor_name: str = Query(..., description="Full name of the tutor"),
    subject_name: str = Query(..., description="Name of the subject"),
    original_start_time: str = Query(..., description="Original start time in ISO format")
):
    db = SessionLocal()
    try:
        # Parse input time
        target_dt = parse_iso_datetime(original_start_time)
        target_time_str = format_db_datetime(target_dt)

        query = text("""
            SELECT s.id, s.student_id, s.tutor_id, s.subject_id, s.start_time, s.end_time, s.timezone, s.status, s.notes
            FROM sessions s
            JOIN users u ON s.tutor_id = u.id
            JOIN subjects sub ON s.subject_id = sub.id
            WHERE u.full_name = :tutor_name
              AND sub.name = :subject_name
              AND s.start_time = :start_time
        """)

        results = db.execute(query, {
            "tutor_name": tutor_name,
            "subject_name": subject_name,
            "start_time": target_time_str
        }).fetchall()

        sessions_list = []
        for row in results:
            sessions_list.append(SessionSearchResponse(
                id=row[0],
                student_id=row[1],
                tutor_id=row[2],
                subject_id=row[3],
                start_time=row[4],
                end_time=row[5],
                timezone=row[6],
                status=row[7],
                notes=row[8]
            ))
        
        return sessions_list

    finally:
        db.close()


@app.put("/sessions/{session_id}/reschedule", response_model=RescheduleResponse, operation_id="reschedule_session")
def reschedule_session(
    session_id: int = Path(..., description="Internal ID of the session"),
    request: RescheduleRequest = ...
):
    db = SessionLocal()
    try:
        # Get current actor
        actor_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not actor_user_id_str:
            raise HTTPException(status_code=401, detail="Missing user context")
        actor_user_id = int(actor_user_id_str)

        # Fetch existing session
        session_query = text("SELECT * FROM sessions WHERE id = :id")
        session_row = db.execute(session_query, {"id": session_id}).fetchone()

        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found")

        # Check if session is reschedulable (status must be scheduled)
        current_status = session_row[7] # status column index based on schema order: id, student_id, tutor_id, subject_id, start_time, end_time, timezone, status...
        # Let's map indices carefully from DDL:
        # 0:id, 1:student_id, 2:tutor_id, 3:subject_id, 4:start_time, 5:end_time, 6:timezone, 7:status, 8:notes, 9:created_at, 10:updated_at
        
        if current_status != 'scheduled':
            raise HTTPException(status_code=400, detail=f"Cannot reschedule session with status '{current_status}'")

        old_start_time = session_row[4]
        old_end_time = session_row[5]
        old_timezone = session_row[6]
        student_id = session_row[1]
        tutor_id = session_row[2]

        # Parse new times
        new_start_dt = parse_iso_datetime(request.new_start_time)
        new_end_dt = parse_iso_datetime(request.new_end_time)
        
        new_start_str = format_db_datetime(new_start_dt)
        new_end_str = format_db_datetime(new_end_dt)

        # Validate end > start
        if new_end_dt <= new_start_dt:
            raise HTTPException(status_code=400, detail="New end time must be after new start time")

        # Determine timezone for new entry (keep same as old unless specified otherwise, but spec doesn't have new_tz param)
        new_timezone = old_timezone

        # Update Session
        update_notes = request.notes if request.notes is not None else session_row[8]
        
        update_session_query = text("""
            UPDATE sessions 
            SET start_time = :new_start, 
                end_time = :new_end, 
                status = 'rescheduled', 
                notes = :notes, 
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """)
        
        db.execute(update_session_query, {
            "new_start": new_start_str,
            "new_end": new_end_str,
            "notes": update_notes,
            "id": session_id
        })

        # Insert into session_reschedules
        insert_reschedule_query = text("""
            INSERT INTO session_reschedules (
                session_id, old_start_time, old_end_time, new_start_time, new_end_time, 
                old_timezone, new_timezone, reason, rescheduled_by, created_at
            ) VALUES (
                :session_id, :old_start, :old_end, :new_start, :new_end, 
                :old_tz, :new_tz, :reason, :rescheduled_by, CURRENT_TIMESTAMP
            )
        """)
        
        result = db.execute(insert_reschedule_query, {
            "session_id": session_id,
            "old_start": old_start_time,
            "old_end": old_end_time,
            "new_start": new_start_str,
            "new_end": new_end_str,
            "old_tz": old_timezone,
            "new_tz": new_timezone,
            "reason": update_notes, # Using notes as reason for reschedule log
            "rescheduled_by": actor_user_id
        })
        
        reschedule_id = result.lastrowid

        # Create Notifications for Student and Tutor
        # Notification Type: 'reschedule'
        # Channel: 'email' (standard assumption for automated reminders/notifications in this context)
        
        notification_message_student = f"Your tutoring session has been rescheduled to {request.new_start_time}."
        notification_message_tutor = f"A tutoring session you are assigned to has been rescheduled to {request.new_start_time}."

        insert_notif_query = text("""
            INSERT INTO notifications (
                session_id, recipient_id, notification_type, channel, subject, message, status, scheduled_for, sent_at, created_at, updated_at
            ) VALUES (
                :session_id, :recipient_id, 'reschedule', 'email', 'Session Rescheduled', :message, 'pending', NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """)

        # Notify Student
        db.execute(insert_notif_query, {
            "session_id": session_id,
            "recipient_id": student_id,
            "message": notification_message_student
        })

        # Notify Tutor
        db.execute(insert_notif_query, {
            "session_id": session_id,
            "recipient_id": tutor_id,
            "message": notification_message_tutor
        })

        db.commit()

        return RescheduleResponse(
            session_id=session_id,
            status="rescheduled",
            reschedule_id=reschedule_id
        )

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
