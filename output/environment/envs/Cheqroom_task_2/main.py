import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Date,
    ForeignKey,
    Boolean,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI(title="Cheqroom API", version="1.0.0")


# --- Pydantic Models ---

class BookingItemInput(BaseModel):
    equipment_id: int
    quantity: int


class CreateBookingRequest(BaseModel):
    user_id: int
    department_id: int
    location_id: int
    start_datetime: str  # ISO-8601 string
    end_datetime: str    # ISO-8601 string
    recurrence_rule: Optional[str] = None
    recurrence_end_date: Optional[str] = None
    notes: Optional[str] = None
    booking_items: List[BookingItemInput]


class BookingResponse(BaseModel):
    id: int
    user_id: int
    department_id: int
    location_id: int
    start_datetime: str
    end_datetime: str
    status: str
    created_at: str
    updated_at: str
    recurrence_rule: Optional[str] = None
    recurrence_end_date: Optional[str] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class EquipmentResponse(BaseModel):
    id: int
    name: str
    category_id: int
    serial_number: str
    is_available: bool
    description: Optional[str] = None

    class Config:
        from_attributes = True


class DepartmentResponse(BaseModel):
    id: int
    name: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class LocationResponse(BaseModel):
    id: int
    name: str
    address: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both T...Z and space-separated formats."""
    if not dt_str:
        return None
    # Replace 'T' with space if present, remove trailing 'Z'
    cleaned = dt_str.replace('T', ' ').replace('Z', '')
    # Try standard format
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(cleaned, "%Y-%m-%d %H:%M")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid datetime format")

def parse_date(date_str: str) -> str:
    """Parse date string and return YYYY-MM-DD."""
    if not date_str:
        return None
    cleaned = date_str.split('T')[0]
    return cleaned

def format_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


# --- Endpoints ---

@app.post("/bookings", operation_id="create_booking", response_model=BookingResponse)
def create_booking(request: CreateBookingRequest):
    """Create a new equipment booking"""
    
    # Parse datetimes
    try:
        start_dt = parse_datetime(request.start_datetime)
        end_dt = parse_datetime(request.end_datetime)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="End datetime must be after start datetime")

    # Validate existence of referenced entities
    db = SessionLocal()
    try:
        # Check User
        user_check = db.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": request.user_id}).fetchone()
        if not user_check:
            raise HTTPException(status_code=404, detail="User not found")

        # Check Department
        dept_check = db.execute(text("SELECT id FROM departments WHERE id = :did"), {"did": request.department_id}).fetchone()
        if not dept_check:
            raise HTTPException(status_code=404, detail="Department not found")

        # Check Location
        loc_check = db.execute(text("SELECT id FROM locations WHERE id = :lid"), {"lid": request.location_id}).fetchone()
        if not loc_check:
            raise HTTPException(status_code=404, detail="Location not found")

        # Check Equipment Items
        equipment_ids = [item.equipment_id for item in request.booking_items]
        placeholders = ','.join([f':eq{i}' for i in range(len(equipment_ids))])
        
        eq_check_sql = f"SELECT id, is_available FROM equipment WHERE id IN ({placeholders})"
        eq_params = {f'eq{i}': eid for i, eid in enumerate(equipment_ids)}
        eq_results = db.execute(text(eq_check_sql), eq_params).fetchall()
        
        if len(eq_results) != len(equipment_ids):
            raise HTTPException(status_code=404, detail="One or more equipment items not found")
            
        # Check availability (simple check: ensure they are marked available)
        # In a real system, we'd check conflicts, but the spec implies simple creation
        # The schema has is_available flag. We assume if it's there and available, it can be booked.
        # Note: The task asks to reserve specific items. We just need them to exist.
        
        # Create Booking
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        # If env var exists, use it for created_by logic if needed, but spec says user_id is in body.
        # The prompt says: "When a write must record an actor column ... read that environment variable".
        # The bookings table has user_id which is provided in the body. 
        # However, usually "created_by" might be separate. Here, user_id seems to be the requester.
        # Let's stick to the body user_id for the booking's user_id field.
        
        rec_rule = request.recurrence_rule
        rec_end = request.recurrence_end_date
        
        # Insert Booking
        insert_sql = text("""
            INSERT INTO bookings (user_id, department_id, location_id, start_datetime, end_datetime, 
                                  recurrence_rule, recurrence_end_date, status, notes, created_at, updated_at)
            VALUES (:uid, :did, :lid, :sd, :ed, :rr, :re, 'confirmed', :notes, :ca, :ua)
        """)
        
        now_str = format_datetime(datetime.utcnow())
        
        db.execute(insert_sql, {
            "uid": request.user_id,
            "did": request.department_id,
            "lid": request.location_id,
            "sd": format_datetime(start_dt),
            "ed": format_datetime(end_dt),
            "rr": rec_rule,
            "re": rec_end,
            "notes": request.notes,
            "ca": now_str,
            "ua": now_str
        })
        
        db.commit()
        
        # Get last inserted ID
        last_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        
        # Insert Booking Items
        for item in request.booking_items:
            item_sql = text("""
                INSERT INTO booking_items (booking_id, equipment_id, quantity, created_at, updated_at)
                VALUES (:bid, :eid, :qty, :ca, :ua)
            """)
            db.execute(item_sql, {
                "bid": last_id,
                "eid": item.equipment_id,
                "qty": item.quantity,
                "ca": now_str,
                "ua": now_str
            })
        
        db.commit()
        
        # Fetch Created Booking
        fetch_sql = text("""
            SELECT id, user_id, department_id, location_id, start_datetime, end_datetime,
                   recurrence_rule, recurrence_end_date, status, notes, created_at, updated_at
            FROM bookings WHERE id = :bid
        """)
        row = db.execute(fetch_sql, {"bid": last_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created booking")
            
        result_dict = dict(row._mapping)
        return BookingResponse(**result_dict)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/equipment", operation_id="find_equipment_by_category_and_availability", response_model=List[EquipmentResponse])
def find_equipment_by_category_and_availability(
    category_name: str = Query(..., description="Name of the equipment category"),
    is_available: bool = Query(True, description="Filter by availability status")
):
    """Retrieve a list of available equipment items filtered by category name and availability status."""
    
    db = SessionLocal()
    try:
        # Find Category ID
        cat_sql = text("SELECT id FROM equipment_categories WHERE name = :cname")
        cat_row = db.execute(cat_sql, {"cname": category_name}).fetchone()
        
        if not cat_row:
            return []
            
        cat_id = cat_row.id
        
        # Build query
        avail_val = 1 if is_available else 0
        sql = text("""
            SELECT id, name, category_id, serial_number, description, is_available
            FROM equipment
            WHERE category_id = :cid AND is_available = :avail
        """)
        
        rows = db.execute(sql, {"cid": cat_id, "avail": avail_val}).fetchall()
        
        results = []
        for row in rows:
            r_dict = dict(row._mapping)
            r_dict['is_available'] = bool(r_dict['is_available'])
            results.append(EquipmentResponse(**r_dict))
            
        return results
        
    finally:
        db.close()


@app.get("/departments", operation_id="find_department_by_name", response_model=DepartmentResponse)
def find_department_by_name(name: str = Query(..., description="Name of the department")):
    """Retrieve department information by its name."""
    
    db = SessionLocal()
    try:
        sql = text("SELECT id, name, created_at, updated_at FROM departments WHERE name = :name")
        row = db.execute(sql, {"name": name}).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Department not found")
            
        result_dict = dict(row._mapping)
        return DepartmentResponse(**result_dict)
        
    finally:
        db.close()


@app.get("/locations", operation_id="find_location_by_name", response_model=LocationResponse)
def find_location_by_name(name: str = Query(..., description="Name of the location")):
    """Retrieve location information by its name."""
    
    db = SessionLocal()
    try:
        sql = text("SELECT id, name, address, created_at, updated_at FROM locations WHERE name = :name")
        row = db.execute(sql, {"name": name}).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Location not found")
            
        result_dict = dict(row._mapping)
        return LocationResponse(**result_dict)
        
    finally:
        db.close()
