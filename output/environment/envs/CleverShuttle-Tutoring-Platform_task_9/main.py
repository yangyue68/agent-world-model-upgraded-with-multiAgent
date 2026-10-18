import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CleverShuttle Tutoring Platform API")


class StudentInfo(BaseModel):
    student_id: int
    full_name: str
    phone: str


class SessionDetail(BaseModel):
    session_id: int
    group_id: int
    group_name: str
    current_start_time: str
    current_end_time: str
    students: List[StudentInfo]


class SessionUpdate(BaseModel):
    session_id: int
    new_start_time: str
    new_end_time: str


class BulkRescheduleRequest(BaseModel):
    session_updates: List[SessionUpdate]


class FailedUpdate(BaseModel):
    session_id: int
    error_message: str


class BulkRescheduleResponse(BaseModel):
    success_count: int
    failed_updates: List[FailedUpdate]


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both 'T...Z' and space-separated formats."""
    dt_str = dt_str.strip()
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    # Try parsing with timezone info first
    try:
        return datetime.fromisoformat(dt_str.replace(' ', 'T'))
    except ValueError:
        pass
    
    # Try without timezone
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    
    try:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {dt_str}")


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="No authenticated user")
    return int(user_id_str)


@app.get("/primary-resource/sessions/bulk-reschedule", response_model=List[SessionDetail], operation_id="bulk_reschedule_sessions")
def bulk_reschedule_sessions(
    subject_name: str = Query(..., description="Subject name to filter sessions"),
    start_date: str = Query(..., description="Start date of the target week (YYYY-MM-DD format)"),
    end_date: str = Query(..., description="End date of the target week (YYYY-MM-DD format)")
):
    db = SessionLocal()
    try:
        # Parse dates
        start_dt = parse_datetime(start_date + " 00:00:00")
        end_dt = parse_datetime(end_date + " 23:59:59")
        
        # Find sessions for the given subject in the date range with status 'pending'
        query = text("""
            SELECT s.id as session_id, g.id as group_id, g.name as group_name, 
                   s.start_time, s.end_time
            FROM sessions s
            JOIN groups g ON s.group_id = g.id
            JOIN subjects sub ON g.subject_id = sub.id
            WHERE sub.name = :subject_name
              AND s.status = 'pending'
              AND s.start_time >= :start_date
              AND s.end_time <= :end_date
            ORDER BY s.start_time
        """)
        
        result = db.execute(query, {
            "subject_name": subject_name,
            "start_date": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end_dt.strftime("%Y-%m-%d %H:%M:%S")
        })
        
        sessions_data = []
        for row in result.fetchall():
            session_id = row.session_id
            group_id = row.group_id
            group_name = row.group_name
            current_start = row.start_time
            current_end = row.end_time
            
            # Get students for this group
            students_query = text("""
                SELECT st.id as student_id, st.full_name, st.phone
                FROM group_students gs
                JOIN students st ON gs.student_id = st.id
                WHERE gs.group_id = :group_id
            """)
            
            students_result = db.execute(students_query, {"group_id": group_id})
            students_list = [
                StudentInfo(
                    student_id=sr.student_id,
                    full_name=sr.full_name,
                    phone=sr.phone
                )
                for sr in students_result.fetchall()
            ]
            
            sessions_data.append(SessionDetail(
                session_id=session_id,
                group_id=group_id,
                group_name=group_name,
                current_start_time=current_start.isoformat() if isinstance(current_start, datetime) else str(current_start),
                current_end_time=current_end.isoformat() if isinstance(current_end, datetime) else str(current_end),
                students=students_list
            ))
        
        return sessions_data
        
    finally:
        db.close()


@app.post("/primary-resource/sessions/bulk-reschedule", response_model=BulkRescheduleResponse, operation_id="execute_bulk_reschedule")
def execute_bulk_reschedule(request_body: BulkRescheduleRequest):
    db = SessionLocal()
    try:
        current_user_id = get_current_user_id()
        
        success_count = 0
        failed_updates = []
        
        for update in request_body.session_updates:
            session_id = update.session_id
            new_start_str = update.new_start_time
            new_end_str = update.new_end_time
            
            try:
                new_start = parse_datetime(new_start_str)
                new_end = parse_datetime(new_end_str)
                
                if new_start >= new_end:
                    failed_updates.append(FailedUpdate(
                        session_id=session_id,
                        error_message="New start time must be before new end time"
                    ))
                    continue
                
                # Check if session exists and is pending
                check_session = text("""
                    SELECT id, group_id, start_time, end_time, status
                    FROM sessions
                    WHERE id = :session_id
                """)
                
                session_row = db.execute(check_session, {"session_id": session_id}).fetchone()
                
                if not session_row:
                    failed_updates.append(FailedUpdate(
                        session_id=session_id,
                        error_message="Session not found"
                    ))
                    continue
                
                if session_row.status != 'pending':
                    failed_updates.append(FailedUpdate(
                        session_id=session_id,
                        error_message=f"Session status is '{session_row.status}', expected 'pending'"
                    ))
                    continue
                
                group_id = session_row.group_id
                
                # Check for conflicts with tutor calendar
                conflict_check = text("""
                    SELECT COUNT(*) as cnt
                    FROM tutor_calendar
                    WHERE user_id = :user_id
                      AND NOT (:new_end <= start_time OR :new_start >= end_time)
                """)
                
                conflict_result = db.execute(conflict_check, {
                    "user_id": current_user_id,
                    "new_start": new_start.strftime("%Y-%m-%d %H:%M:%S"),
                    "new_end": new_end.strftime("%Y-%m-%d %H:%M:%S")
                }).fetchone()
                
                if conflict_result.cnt > 0:
                    failed_updates.append(FailedUpdate(
                        session_id=session_id,
                        error_message="Conflict with tutor's personal calendar"
                    ))
                    continue
                
                # Check for conflicts with other sessions for the same group
                other_sessions_conflict = text("""
                    SELECT COUNT(*) as cnt
                    FROM sessions
                    WHERE group_id = :group_id
                      AND id != :session_id
                      AND status IN ('pending', 'confirmed')
                      AND NOT (:new_end <= start_time OR :new_start >= end_time)
                """)
                
                other_conflict_result = db.execute(other_sessions_conflict, {
                    "group_id": group_id,
                    "session_id": session_id,
                    "new_start": new_start.strftime("%Y-%m-%d %H:%M:%S"),
                    "new_end": new_end.strftime("%Y-%m-%d %H:%M:%S")
                }).fetchone()
                
                if other_conflict_result.cnt > 0:
                    failed_updates.append(FailedUpdate(
                        session_id=session_id,
                        error_message="Conflict with another session for the same group"
                    ))
                    continue
                
                # Update the session
                update_session = text("""
                    UPDATE sessions
                    SET start_time = :new_start,
                        end_time = :new_end,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :session_id
                """)
                
                db.execute(update_session, {
                    "new_start": new_start.strftime("%Y-%m-%d %H:%M:%S"),
                    "new_end": new_end.strftime("%Y-%m-%d %H:%M:%S"),
                    "session_id": session_id
                })
                
                # Get students for this group to send SMS notifications
                students_query = text("""
                    SELECT st.id as student_id, st.full_name
                    FROM group_students gs
                    JOIN students st ON gs.student_id = st.id
                    WHERE gs.group_id = :group_id
                """)
                
                students_result = db.execute(students_query, {"group_id": group_id}).fetchall()
                
                # Create SMS notification records
                message = f"Your Algebra I session has been rescheduled to {new_start.strftime('%Y-%m-%d %H:%M')}."
                
                insert_sms = text("""
                    INSERT INTO sms_notifications (student_id, session_id, message, status, created_at, updated_at)
                    VALUES (:student_id, :session_id, :message, 'sent', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """)
                
                for student in students_result:
                    db.execute(insert_sms, {
                        "student_id": student.student_id,
                        "session_id": session_id,
                        "message": message
                    })
                
                success_count += 1
                
            except Exception as e:
                failed_updates.append(FailedUpdate(
                    session_id=session_id,
                    error_message=str(e)
                ))
        
        db.commit()
        
        return BulkRescheduleResponse(
            success_count=success_count,
            failed_updates=failed_updates
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
