import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CareCircle Clinic Manager API")


class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    doctor_id: int
    room_id: Optional[int] = None
    appointment_datetime: str
    duration_minutes: int
    status: str
    notes: Optional[str] = None


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both 'T' separator and space."""
    # Replace 'Z' with '+00:00' if present for fromisoformat compatibility in older Python versions
    # However, standard fromisoformat handles Z in newer versions. 
    # To be safe across environments, we can try replacing T/Z or just use a robust parser.
    # The prompt asks to support both T...Z and space-separated forms.
    
    dt_str = dt_str.strip()
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    # Try parsing with fromisoformat first (handles 'T')
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        pass
    
    # Handle space separated form e.g., "2025-01-22 15:30:00"
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
        
    # Fallback attempts could go here, but raising error is appropriate for invalid input
    raise ValueError(f"Invalid datetime format: {dt_str}")


@app.put("/appointments/{appointment_id}", response_model=AppointmentResponse, operation_id="reschedule_appointment")
def reschedule_appointment(
    appointment_id: int = Path(..., description="Internal ID of the appointment to reschedule"),
    new_datetime: str = Body(..., description="New appointment datetime in ISO format (e.g., 2025-01-22T15:30:00)"),
    room_id: Optional[int] = Body(None, description="Optional room ID to assign to the appointment. If not provided, no room change occurs.")
):
    db = SessionLocal()
    try:
        # Parse the new datetime
        try:
            parsed_new_dt = parse_iso_datetime(new_datetime)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid datetime format: {str(e)}")

        # Format back to standard DB string format YYYY-MM-DD HH:MM:SS
        formatted_new_dt = parsed_new_dt.strftime("%Y-%m-%d %H:%M:%S")

        # Fetch current appointment details including duration and doctor_id
        stmt_get_appt = text("""
            SELECT id, patient_id, doctor_id, room_id, appointment_datetime, duration_minutes, status, notes
            FROM appointments
            WHERE id = :aid
        """)
        result = db.execute(stmt_get_appt, {"aid": appointment_id})
        appt_row = result.fetchone()

        if not appt_row:
            raise HTTPException(status_code=404, detail="Appointment not found")

        current_doctor_id = appt_row[2]
        current_duration = appt_row[5]
        
        # Calculate end time for conflict checking
        start_time = parsed_new_dt
        end_time = start_time + timedelta(minutes=current_duration)
        formatted_end_time = end_time.strftime("%Y-%m-%d %H:%M:%S")

        # Check for Doctor Conflict
        # Exclude the current appointment itself from the check
        stmt_check_doc_conflict = text("""
            SELECT COUNT(*) 
            FROM appointments
            WHERE doctor_id = :did
              AND id != :aid
              AND status NOT IN ('cancelled', 'no_show')
              AND appointment_datetime < :end_dt
              AND (datetime(appointment_datetime, '+' || duration_minutes || ' minutes')) > :start_dt
        """)
        doc_conflict_count = db.execute(
            stmt_check_doc_conflict,
            {
                "did": current_doctor_id,
                "aid": appointment_id,
                "start_dt": formatted_new_dt,
                "end_dt": formatted_end_time
            }
        ).scalar()

        if doc_conflict_count > 0:
            raise HTTPException(status_code=409, detail="Doctor has a conflicting appointment at the requested time.")

        # Determine final room_id
        final_room_id = room_id if room_id is not None else appt_row[3]

        # Check Room Availability if a room is being assigned/changed
        if final_room_id is not None:
            # Verify room exists and is available globally? 
            # The spec says "update the room assignment to Room 1 if available".
            # Usually "available" means is_available=1 in the rooms table.
            # And also no other appointment using that room at that time.
            
            stmt_check_room_exists = text("SELECT is_available FROM rooms WHERE id = :rid")
            room_avail_flag = db.execute(stmt_check_room_exists, {"rid": final_room_id}).scalar()
            
            if room_avail_flag is None:
                 raise HTTPException(status_code=404, detail="Room not found")
            
            if room_avail_flag == 0:
                raise HTTPException(status_code=409, detail="Room is currently marked as unavailable.")

            # Check for Room Time Conflict
            stmt_check_room_conflict = text("""
                SELECT COUNT(*) 
                FROM appointments
                WHERE room_id = :rid
                  AND id != :aid
                  AND status NOT IN ('cancelled', 'no_show')
                  AND appointment_datetime < :end_dt
                  AND (datetime(appointment_datetime, '+' || duration_minutes || ' minutes')) > :start_dt
            """)
            room_conflict_count = db.execute(
                stmt_check_room_conflict,
                {
                    "rid": final_room_id,
                    "aid": appointment_id,
                    "start_dt": formatted_new_dt,
                    "end_dt": formatted_end_time
                }
            ).scalar()

            if room_conflict_count > 0:
                raise HTTPException(status_code=409, detail="Room has a conflicting appointment at the requested time.")

        # Perform Update
        stmt_update = text("""
            UPDATE appointments
            SET appointment_datetime = :new_dt,
                room_id = :r_id,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :aid
        """)
        
        db.execute(
            stmt_update,
            {
                "new_dt": formatted_new_dt,
                "r_id": final_room_id,
                "aid": appointment_id
            }
        )
        db.commit()

        # Fetch updated record to return
        stmt_fetch_updated = text("""
            SELECT id, patient_id, doctor_id, room_id, appointment_datetime, duration_minutes, status, notes
            FROM appointments
            WHERE id = :aid
        """)
        updated_row = db.execute(stmt_fetch_updated, {"aid": appointment_id}).fetchone()
        
        if not updated_row:
             raise HTTPException(status_code=500, detail="Failed to retrieve updated appointment")

        # Construct Response
        # appointment_datetime in DB might be returned as string or datetime object depending on driver/config
        # We ensure it's a string in ISO-like format expected by Pydantic/OpenAPI usually, 
        # but the schema says type: string. Let's format it consistently.
        raw_dt = updated_row[4]
        if isinstance(raw_dt, datetime):
            resp_dt_str = raw_dt.isoformat(sep=' ')
        else:
            resp_dt_str = str(raw_dt)

        return AppointmentResponse(
            id=updated_row[0],
            patient_id=updated_row[1],
            doctor_id=updated_row[2],
            room_id=updated_row[3],
            appointment_datetime=resp_dt_str,
            duration_minutes=updated_row[5],
            status=updated_row[6],
            notes=updated_row[7]
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
