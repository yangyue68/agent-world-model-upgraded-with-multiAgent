import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from contextlib import asynccontextmanager

# Initialize engine with the provided DATABASE_PATH
engine = create_engine(os.environ.get("DATABASE_PATH"))

app = FastAPI(title="CareCircle Clinic Manager")


class PatientSearchResult(BaseModel):
    id: int
    first_name: str
    last_name: str


class AppointmentType(BaseModel):
    id: int
    name: str
    duration_minutes: int


class WaitlistRequest(BaseModel):
    patient_id: int
    appointment_type_id: int
    preferred_time_of_day: str
    preferred_hygienist_id: Optional[int] = None


class WaitlistResponse(BaseModel):
    waitlist_entry_id: int
    notification_id: int


@app.get("/patients/search", response_model=List[PatientSearchResult], operation_id="search_patients")
def search_patients(
    first_name: Optional[str] = Query(None, description="Patient's first name"),
    last_name: Optional[str] = Query(None, description="Patient's last name")
):
    """
    Find patients by first and/or last name and return matching IDs and names.
    """
    query_parts = ["SELECT id, first_name, last_name FROM patients WHERE 1=1"]
    params = {}

    if first_name:
        query_parts.append("AND lower(first_name) LIKE :first_name_pattern")
        params["first_name_pattern"] = f"%{first_name.lower()}%"
    
    if last_name:
        query_parts.append("AND lower(last_name) LIKE :last_name_pattern")
        params["last_name_pattern"] = f"%{last_name.lower()}%"

    sql = " ".join(query_parts)
    
    with engine.connect() as conn:
        result = conn.execute(text(sql), params).fetchall()
        
    return [
        PatientSearchResult(id=row.id, first_name=row.first_name, last_name=row.last_name)
        for row in result
    ]


@app.get("/appointment_types", response_model=List[AppointmentType], operation_id="find_appointment_types")
def find_appointment_types():
    """
    Retrieve all available appointment types with their IDs and durations.
    """
    sql = "SELECT id, name, duration_minutes FROM appointment_types"
    
    with engine.connect() as conn:
        result = conn.execute(text(sql)).fetchall()
        
    return [
        AppointmentType(id=row.id, name=row.name, duration_minutes=row.duration_minutes)
        for row in result
    ]


@app.post("/waitlist", response_model=WaitlistResponse, operation_id="add_patient_to_waitlist")
def add_patient_to_waitlist(request: WaitlistRequest):
    """
    Registers patient Emily Chen for a cleaning appointment with any hygienist, 
    preferring morning slots, and creates a notification entry for when a slot becomes available.
    """
    # Validate that the patient exists
    with engine.connect() as conn:
        patient_check = conn.execute(
            text("SELECT id FROM patients WHERE id = :pid"),
            {"pid": request.patient_id}
        ).fetchone()
        
        if not patient_check:
            raise HTTPException(status_code=404, detail="Patient not found")

        # Validate appointment type exists
        appt_type_check = conn.execute(
            text("SELECT id FROM appointment_types WHERE id = :atid"),
            {"atid": request.appointment_type_id}
        ).fetchone()
        
        if not appt_type_check:
            raise HTTPException(status_code=404, detail="Appointment type not found")

        # Insert into waitlist_entries
        insert_waitlist_sql = """
            INSERT INTO waitlist_entries (
                patient_id, 
                appointment_type_id, 
                preferred_hygienist_id, 
                preferred_time_of_day, 
                status, 
                notes
            ) VALUES (
                :patient_id, 
                :appointment_type_id, 
                :preferred_hygienist_id, 
                :preferred_time_of_day, 
                'waiting', 
                :notes
            )
        """
        
        # Construct notes based on preferences
        note_text = f"Preferred time: {request.preferred_time_of_day}"
        if request.preferred_hygienist_id:
            note_text += f", Preferred Hygienist ID: {request.preferred_hygienist_id}"
            
        waitlist_params = {
            "patient_id": request.patient_id,
            "appointment_type_id": request.appointment_type_id,
            "preferred_hygienist_id": request.preferred_hygienist_id,
            "preferred_time_of_day": request.preferred_time_of_day,
            "notes": note_text
        }
        
        res = conn.execute(text(insert_waitlist_sql), waitlist_params)
        waitlist_entry_id = res.lastrowid
        
        # Create notification entry
        # The spec says "notify her when a slot becomes available". 
        # Since we are adding to waitlist now, we create a pending notification 
        # linked to this waitlist entry.
        insert_notification_sql = """
            INSERT INTO notifications (
                patient_id, 
                user_id, 
                waitlist_entry_id, 
                channel, 
                subject, 
                message, 
                status
            ) VALUES (
                :patient_id, 
                NULL, 
                :waitlist_entry_id, 
                'email', 
                'Waitlist Confirmation', 
                :message, 
                'pending'
            )
        """
        
        # Get patient details for personalized message
        patient_details = conn.execute(
            text("SELECT first_name, last_name FROM patients WHERE id = :pid"),
            {"pid": request.patient_id}
        ).fetchone()
        
        full_name = f"{patient_details.first_name} {patient_details.last_name}"
        msg = f"Dear {full_name}, you have been added to our waitlist for a cleaning appointment. We will notify you when a suitable slot becomes available."
        
        notif_params = {
            "patient_id": request.patient_id,
            "waitlist_entry_id": waitlist_entry_id,
            "message": msg
        }
        
        res_notif = conn.execute(text(insert_notification_sql), notif_params)
        notification_id = res_notif.lastrowid
        
        conn.commit()
        
    return WaitlistResponse(
        waitlist_entry_id=waitlist_entry_id,
        notification_id=notification_id
    )
