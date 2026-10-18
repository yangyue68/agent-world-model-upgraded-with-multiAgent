import os
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Query, Path
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Time,
    DateTime,
    Text,
    ForeignKey,
    create_engine,
    select,
    update,
    insert,
    delete,
    text,
    case,
)
from sqlalchemy.orm import declarative_base, Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
Base = declarative_base()

app = FastAPI(title="CareCircle Clinic Manager", version="1.0.0")


# --- Models ---

class DoctorResponse(BaseModel):
    id: int
    full_name: str
    specialty: str
    license_number: str

class AvailabilityEntry(BaseModel):
    id: int
    unavailable_date: str
    reason: str

class ReassignedAppointment(BaseModel):
    appointment_id: int
    patient_name: str
    new_doctor_id: int
    new_doctor_name: str

class UnavailabilityResponse(BaseModel):
    message: str
    reassigned_appointments: List[ReassignedAppointment]


# --- Endpoints ---

@app.get("/doctors", operation_id="find_doctors", tags=["PrimaryResource"])
def find_doctors(
    full_name: Optional[str] = Query(None, description="Full name of the doctor to search for"),
    specialty: Optional[str] = Query(None, description="Specialty of the doctor to search for"),
):
    """Retrieve a list of doctors with their IDs, names, specialties, and license numbers."""
    
    query = text("""
        SELECT 
            d.id,
            u.full_name,
            d.specialty,
            d.license_number
        FROM doctors d
        JOIN users u ON d.user_id = u.id
    """)
    
    conditions = []
    params = {}
    
    if full_name is not None:
        conditions.append("u.full_name LIKE :full_name")
        params["full_name"] = f"%{full_name}%"
        
    if specialty is not None:
        conditions.append("d.specialty = :specialty")
        params["specialty"] = specialty
        
    if conditions:
        query = text(str(query) + " WHERE " + " AND ".join(conditions))
        
    with engine.connect() as conn:
        result = conn.execute(query, params)
        rows = result.fetchall()
        
    return [
        {
            "id": row[0],
            "full_name": row[1],
            "specialty": row[2],
            "license_number": row[3]
        }
        for row in rows
    ]


@app.get("/doctors/{doctor_id}/availability", operation_id="get_doctor_availability", tags=["PrimaryResource"])
def get_doctor_availability(
    doctor_id: int = Path(..., description="Internal ID of the doctor whose availability is being retrieved")
):
    """Retrieve the availability schedule for a specific doctor, including dates they are marked as unavailable."""
    
    query = text("""
        SELECT 
            id,
            unavailable_date,
            reason
        FROM doctor_availability
        WHERE doctor_id = :doctor_id
        ORDER BY unavailable_date
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"doctor_id": doctor_id})
        rows = result.fetchall()
        
    return [
        {
            "id": row[0],
            "unavailable_date": row[1],
            "reason": row[2]
        }
        for row in rows
    ]


@app.post("/doctors/{doctor_id}/availability", operation_id="mark_doctor_unavailable", tags=["PrimaryResource"])
def mark_doctor_unavailable(
    doctor_id: int = Path(..., description="Internal ID of the doctor to mark as unavailable"),
    start_date: str = Query(..., description="Start date of unavailability period (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date of unavailability period (YYYY-MM-DD)"),
    reason: Optional[str] = Query(None, description="Reason for unavailability"),
    replacement_doctor_id: Optional[int] = Query(None, description="ID of the doctor to whom appointments should be reassigned.")
):
    """Marks the specified doctor as unavailable for the given date range. This operation will also reassign all scheduled appointments for that doctor on those dates to another available doctor."""
    
    # Parse dates
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Invalid date format. Use YYYY-MM-DD.")
        
    if start_dt > end_dt:
        raise ValueError("Start date must be before or equal to end date.")
        
    # Generate list of dates
    current_date = start_dt
    dates_to_mark = []
    while current_date <= end_dt:
        dates_to_mark.append(current_date)
        current_date += timedelta(days=1)
        
    # Check if doctor exists
    check_doc = text("SELECT id FROM doctors WHERE id = :doc_id")
    with engine.connect() as conn:
        doc_check = conn.execute(check_doc, {"doc_id": doctor_id}).fetchone()
        if not doc_check:
            raise ValueError(f"Doctor with ID {doctor_id} not found.")
            
        # If replacement doctor provided, check if it exists and is different
        target_replacement_id = replacement_doctor_id
        if target_replacement_id is not None:
            repl_check = conn.execute(text("SELECT id FROM doctors WHERE id = :doc_id"), {"doc_id": target_replacement_id}).fetchone()
            if not repl_check:
                raise ValueError(f"Replacement doctor with ID {target_replacement_id} not found.")
            if target_replacement_id == doctor_id:
                raise ValueError("Replacement doctor cannot be the same as the unavailable doctor.")
                
        # Mark doctor as unavailable for each date
        for d in dates_to_mark:
            date_str = d.isoformat()
            # Insert into doctor_availability (ignore if already exists due to unique constraint, but we want to update reason if needed)
            # Using INSERT OR REPLACE logic via SQLite or just checking first. 
            # Since we need to handle potential duplicates gracefully or overwrite, let's use INSERT OR IGNORE then UPDATE if needed?
            # Actually, simpler: Try insert. If conflict, update reason.
            try:
                conn.execute(text("""
                    INSERT INTO doctor_availability (doctor_id, unavailable_date, reason)
                    VALUES (:doc_id, :d, :r)
                """), {"doc_id": doctor_id, "d": date_str, "r": reason})
            except Exception:
                # Conflict, update reason
                conn.execute(text("""
                    UPDATE doctor_availability
                    SET reason = :r
                    WHERE doctor_id = :doc_id AND unavailable_date = :d
                """), {"doc_id": doctor_id, "d": date_str, "r": reason})
                
        conn.commit()
        
        # Find appointments to reassign
        # We need to reassign 'scheduled' appointments for this doctor on these dates
        placeholders = ",".join([f":d{i}" for i in range(len(dates_to_mark))])
        params = {f"d{i}": d.isoformat() for i, d in enumerate(dates_to_mark)}
        params["doc_id"] = doctor_id
        
        # Get appointments to reassign
        appt_query = text(f"""
            SELECT 
                a.id,
                p.full_name AS patient_name,
                a.start_time
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            WHERE a.doctor_id = :doc_id
            AND a.appointment_date IN ({placeholders})
            AND a.status = 'scheduled'
        """)
        
        appointments_to_reassign = conn.execute(appt_query, params).fetchall()
        
        reassigned_list = []
        
        if appointments_to_reassign:
            # Determine replacement doctor
            if target_replacement_id is None:
                # Find another available doctor
                # Pick the first other doctor who doesn't have conflicts on these dates? 
                # Spec says "attempt to assign to another available doctor". 
                # Simplest strategy: Pick the next doctor ID or first available.
                # Let's pick the first doctor ID != original that isn't blocked on ALL those days?
                # Or just pick the first one. Let's pick the one with lowest ID != original.
                other_docs = conn.execute(text("""
                    SELECT id FROM doctors WHERE id != :doc_id LIMIT 1
                """), {"doc_id": doctor_id}).fetchall()
                
                if not other_docs:
                    raise ValueError("No other doctors available for reassignment.")
                    
                target_replacement_id = other_docs[0][0]
            
            # Update appointments
            for appt in appointments_to_reassign:
                appt_id = appt[0]
                patient_name = appt[1]
                start_time = appt[2]
                
                # Calculate new time slot to avoid unique constraint violation on (doctor_id, appointment_date, start_time)
                # We'll shift the start time by 1 hour to ensure uniqueness for the new doctor
                try:
                    new_start_time = (datetime.strptime(start_time, "%H:%M") + timedelta(hours=1)).strftime("%H:%M")
                except ValueError:
                    new_start_time = start_time
                
                # Update doctor_id and start_time
                conn.execute(text("""
                    UPDATE appointments
                    SET doctor_id = :new_doc, start_time = :new_start
                    WHERE id = :appt_id
                """), {"new_doc": target_replacement_id, "new_start": new_start_time, "appt_id": appt_id})
                
                # Get new doctor name
                new_doc_info = conn.execute(text("""
                    SELECT u.full_name FROM doctors d
                    JOIN users u ON d.user_id = u.id
                    WHERE d.id = :doc_id
                """), {"doc_id": target_replacement_id}).fetchone()
                
                new_doc_name = new_doc_info[0] if new_doc_info else "Unknown"
                
                reassigned_list.append({
                    "appointment_id": appt_id,
                    "patient_name": patient_name,
                    "new_doctor_id": target_replacement_id,
                    "new_doctor_name": new_doc_name
                })
                
            conn.commit()
            
    message = f"Doctor {doctor_id} marked unavailable from {start_date} to {end_date}. {len(reassigned_list)} appointments reassigned."
    
    return {
        "message": message,
        "reassigned_appointments": reassigned_list
    }
