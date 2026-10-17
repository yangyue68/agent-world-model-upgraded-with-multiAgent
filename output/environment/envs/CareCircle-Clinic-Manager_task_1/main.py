import os
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CareCircle Clinic Manager API")


# --- Pydantic Models for Requests/Responses ---

class AppointmentCreate(BaseModel):
    patient_id: int
    provider_id: int
    room_id: int
    start_time: str  # ISO-8601 string
    end_time: str    # ISO-8601 string
    notes: Optional[str] = None


class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    provider_id: int
    room_id: int
    start_time: str
    end_time: str
    status: str
    notes: Optional[str]
    created_by: int
    created_at: str
    updated_at: str


class PatientSearchResult(BaseModel):
    id: int
    first_name: str
    last_name: str


class ProviderSearchResult(BaseModel):
    id: int
    full_name: str
    specialty: Optional[str]


class RoomInfo(BaseModel):
    id: int
    name: str
    location: Optional[str]
    capacity: int


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime strings, handling both 'T' and space separators."""
    if dt_str is None:
        return None
    
    # Replace T with space to normalize, then strip Z if present
    normalized = dt_str.replace('T', ' ').replace('Z', '')
    
    # Try common formats
    formats = [
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M"
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
            
    raise ValueError(f"Could not parse datetime string: {dt_str}")


def format_datetime(dt_obj: datetime) -> str:
    """Format datetime object to ISO-8601 string without microseconds if zero."""
    if dt_obj is None:
        return None
    # Return standard ISO format
    return dt_obj.isoformat(sep=' ')


# --- Endpoints ---

@app.post("/appointments", response_model=AppointmentResponse, operation_id="schedule_followup_appointment")
def schedule_appointment(appointment_data: AppointmentCreate):
    """
    Schedule a 30-minute follow-up appointment.
    Ensures no conflicts with existing appointments for the same provider or room.
    """
    current_user_id_env = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_env:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_env)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user ID format")

    # Parse datetimes
    try:
        start_dt = parse_datetime(appointment_data.start_time)
        end_dt = parse_datetime(appointment_data.end_time)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    db = next(get_db())
    try:
        # Verify Patient exists
        patient_exists = db.execute(text("SELECT 1 FROM patients WHERE id = :pid"), {"pid": appointment_data.patient_id}).fetchone()
        if not patient_exists:
            raise HTTPException(status_code=404, detail=f"Patient with ID {appointment_data.patient_id} not found")

        # Verify Provider exists
        provider_exists = db.execute(text("SELECT 1 FROM providers WHERE id = :prid"), {"prid": appointment_data.provider_id}).fetchone()
        if not provider_exists:
            raise HTTPException(status_code=404, detail=f"Provider with ID {appointment_data.provider_id} not found")

        # Verify Room exists
        room_exists = db.execute(text("SELECT 1 FROM rooms WHERE id = :rid"), {"rid": appointment_data.room_id}).fetchone()
        if not room_exists:
            raise HTTPException(status_code=404, detail=f"Room with ID {appointment_data.room_id} not found")

        # Check for conflicts
        # Conflict definition: New interval overlaps with any existing non-cancelled/non-no_show appointment?
        # Usually, we check against all active appointments. The spec says "ensuring no conflicts".
        # We will assume cancelled/no_show don't block slots, but scheduled/confirmed/completed do.
        # However, typically completed appointments are past. Let's stick to checking overlap logic strictly.
        # Overlap condition: (new_start < existing_end) AND (new_end > existing_start)
        
        conflict_query = text("""
            SELECT id 
            FROM appointments 
            WHERE status NOT IN ('cancelled', 'no_show')
              AND (
                  (provider_id = :provider_id AND start_time < :end_time AND end_time > :start_time)
                  OR
                  (room_id = :room_id AND start_time < :end_time AND end_time > :start_time)
              )
        """)
        
        params = {
            "provider_id": appointment_data.provider_id,
            "room_id": appointment_data.room_id,
            "start_time": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end_dt.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        conflict_row = db.execute(conflict_query, params).fetchone()
        if conflict_row:
            raise HTTPException(status_code=409, detail="Conflict detected: Time slot is already booked for this provider or room.")

        # Insert new appointment
        insert_query = text("""
            INSERT INTO appointments (patient_id, provider_id, room_id, start_time, end_time, status, notes, created_by, created_at, updated_at)
            VALUES (:patient_id, :provider_id, :room_id, :start_time, :end_time, 'scheduled', :notes, :created_by, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        
        insert_params = {
            "patient_id": appointment_data.patient_id,
            "provider_id": appointment_data.provider_id,
            "room_id": appointment_data.room_id,
            "start_time": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "notes": appointment_data.notes,
            "created_by": current_user_id
        }
        
        result = db.execute(insert_query, insert_params)
        db.commit()
        
        new_id = result.lastrowid
        
        # Fetch the created record to return full details including timestamps generated by DB
        fetch_query = text("""
            SELECT id, patient_id, provider_id, room_id, start_time, end_time, status, notes, created_by, created_at, updated_at
            FROM appointments
            WHERE id = :id
        """)
        
        row = db.execute(fetch_query, {"id": new_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created appointment")

        # Map row to response model
        # Note: SQLite returns strings for DATETIME columns usually depending on driver/config, 
        # but SQLAlchemy Core often handles conversion. If raw strings come back, we might need parsing.
        # Assuming standard behavior where they are returned as expected types or strings.
        # To be safe, let's ensure they are formatted correctly if they are datetime objects.
        
        def format_val(val):
            if isinstance(val, datetime):
                return val.isoformat(sep=' ')
            return val

        response_data = {
            "id": row[0],
            "patient_id": row[1],
            "provider_id": row[2],
            "room_id": row[3],
            "start_time": format_val(row[4]),
            "end_time": format_val(row[5]),
            "status": row[6],
            "notes": row[7],
            "created_by": row[8],
            "created_at": format_val(row[9]),
            "updated_at": format_val(row[10])
        }
        
        return AppointmentResponse(**response_data)

    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/patients/search", response_model=List[PatientSearchResult], operation_id="search_patients")
def search_patients(name: str = Query(..., description="Partial or full name of the patient to search for")):
    """
    Find patients whose last name or first name matches the provided search term.
    """
    db = next(get_db())
    try:
        query = text("""
            SELECT id, first_name, last_name 
            FROM patients 
            WHERE lower(first_name) LIKE :pattern OR lower(last_name) LIKE :pattern
        """)
        
        pattern = f"%{name.lower()}%"
        results = db.execute(query, {"pattern": pattern}).fetchall()
        
        return [
            PatientSearchResult(id=r[0], first_name=r[1], last_name=r[2])
            for r in results
        ]
    finally:
        db.close()


@app.get("/providers/search", response_model=List[ProviderSearchResult], operation_id="search_providers")
def search_providers(term: str = Query(..., description="Partial or full name or specialty of the provider to search for")):
    """
    Find providers whose full name or specialty matches the provided search term.
    Joins with users table to get full_name.
    """
    db = next(get_db())
    try:
        query = text("""
            SELECT p.id, u.full_name, p.specialty
            FROM providers p
            JOIN users u ON p.user_id = u.id
            WHERE lower(u.full_name) LIKE :pattern OR lower(p.specialty) LIKE :pattern
        """)
        
        pattern = f"%{term.lower()}%"
        results = db.execute(query, {"pattern": pattern}).fetchall()
        
        return [
            ProviderSearchResult(id=r[0], full_name=r[1], specialty=r[2])
            for r in results
        ]
    finally:
        db.close()


@app.get("/rooms", response_model=List[RoomInfo], operation_id="find_rooms")
def find_rooms():
    """
    Retrieve a list of all rooms including their IDs, names, locations, and capacities.
    """
    db = next(get_db())
    try:
        query = text("SELECT id, name, location, capacity FROM rooms ORDER BY id")
        results = db.execute(query).fetchall()
        
        return [
            RoomInfo(id=r[0], name=r[1], location=r[2], capacity=r[3])
            for r in results
        ]
    finally:
        db.close()
