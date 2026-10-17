import os
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Calendly API Simulation")


# --- Pydantic Models ---

class EventTypeCreate(BaseModel):
    user_id: int
    name: str
    description: Optional[str] = None
    duration: int
    timezone: str = "UTC"


class TimeRange(BaseModel):
    start_time: str  # HH:MM
    end_time: str    # HH:MM


class AvailabilityUpdateRequest(BaseModel):
    start_date: Optional[str] = None  # YYYY-MM-DD
    days_to_disable: Optional[List[int]] = None
    time_range: Optional[TimeRange] = None


class RuleResponse(BaseModel):
    id: int
    day_of_week: int
    start_time: str
    end_time: str


class UpdatedAvailabilityResponse(BaseModel):
    event_type_id: int
    schedule_id: int
    rules: List[RuleResponse]


class AvailabilityUpdateResponse(BaseModel):
    message: str
    updated_availability: UpdatedAvailabilityResponse


class GetAvailabilityResponse(BaseModel):
    event_type_id: int
    schedule_id: int
    rules: List[RuleResponse]


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def calculate_next_monday(reference_date_str: Optional[str]) -> datetime:
    """
    Calculates the next Monday relative to today or a provided reference date.
    If no reference date is provided, uses current system date.
    Returns a datetime object set to midnight of that Monday.
    """
    if reference_date_str:
        try:
            ref_date = datetime.strptime(reference_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD.")
    else:
        ref_date = datetime.now().date()

    # Find the next Monday
    # weekday(): Mon=0, Sun=6
    current_weekday = ref_date.weekday()
    
    # Calculate days until next Monday
    # If today is Monday (0), "next Monday" usually implies the upcoming one (today + 7).
    # If today is Sunday (6), next Monday is tomorrow (+1).
    # Standard interpretation: The first Monday occurring after the current date.
    days_ahead = 0 - current_weekday 
    if days_ahead <= 0:  
        days_ahead += 7
    
    next_monday_date = ref_date + timedelta(days=days_ahead)
    return datetime.combine(next_monday_date, datetime.min.time())


# --- Endpoints ---

@app.post("/event_types", response_model=dict, operation_id="create_event_type")
def create_event_type(payload: EventTypeCreate):
    """
    Create a new event type with specified details including duration and timezone.
    """
    db = SessionLocal()
    try:
        # Insert into event_types
        stmt = text("""
            INSERT INTO event_types (user_id, name, description, duration, timezone, created_at, updated_at)
            VALUES (:user_id, :name, :description, :duration, :timezone, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        
        result = db.execute(stmt, {
            "user_id": payload.user_id,
            "name": payload.name,
            "description": payload.description,
            "duration": payload.duration,
            "timezone": payload.timezone
        })
        db.commit()
        
        new_id = result.lastrowid
        
        # Fetch back to ensure consistency
        fetch_stmt = text("SELECT * FROM event_types WHERE id = :id")
        row = db.execute(fetch_stmt, {"id": new_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created event type")
            
        return {
            "id": row.id,
            "user_id": row.user_id,
            "name": row.name,
            "description": row.description,
            "duration": row.duration,
            "timezone": row.timezone
        }
    finally:
        db.close()


@app.put("/event_types/{event_type_id}/availability", response_model=AvailabilityUpdateResponse, operation_id="update_event_type_availability")
def update_event_type_availability(
    event_type_id: int = Path(..., description="The ID of the event type to update"),
    payload: AvailabilityUpdateRequest = ...
):
    """
    Update the availability schedule for a specific event type.
    """
    db = SessionLocal()
    try:
        # 1. Verify Event Type exists
        et_stmt = text("SELECT id, name, timezone FROM event_types WHERE id = :id")
        et_row = db.execute(et_stmt, {"id": event_type_id}).fetchone()
        if not et_row:
            raise HTTPException(status_code=404, detail="Event type not found")

        # 2. Find associated Schedule via event_type_availability
        eta_stmt = text("SELECT schedule_id FROM event_type_availability WHERE event_type_id = :et_id LIMIT 1")
        eta_row = db.execute(eta_stmt, {"et_id": event_type_id}).fetchone()
        
        if not eta_row:
             raise HTTPException(status_code=404, detail="No availability schedule linked to this event type")

        schedule_id = eta_row.schedule_id

        # 3. Process Updates based on Payload
        
        # A. Disable Days
        if payload.days_to_disable:
            for day in payload.days_to_disable:
                del_single = text("DELETE FROM availability_rules WHERE schedule_id = :sid AND day_of_week = :day")
                db.execute(del_single, {"sid": schedule_id, "day": day})
        
        # B. Restrict Time Range
        # If a time range is provided, we update all remaining rules for this schedule to fit within that range.
        # Note: The task says "restrict bookings to only 30-minute slots between 9 AM and 4 PM".
        # This implies modifying the start/end times of existing active rules to clamp them to [09:00, 16:00].
        if payload.time_range:
            new_start = payload.time_range.start_time
            new_end = payload.time_range.end_time
            
            # Fetch current rules again to see what remains after deletions
            rules_stmt = text("SELECT id, day_of_week, start_time, end_time FROM availability_rules WHERE schedule_id = :sid ORDER BY day_of_week, start_time")
            current_rules = db.execute(rules_stmt, {"sid": schedule_id}).fetchall()
            
            def to_minutes(t):
                h, m = map(int, t.split(':'))
                return h * 60 + m
                
            ns_min = to_minutes(new_start)
            ne_min = to_minutes(new_end)

            for rule in current_rules:
                r_start = rule.start_time
                r_end = rule.end_time
                
                rs_min = to_minutes(r_start)
                re_min = to_minutes(r_end)
                
                # Clamp the rule's time to the new global window
                # If the rule was completely outside, it effectively becomes invalid or zero-length?
                # Usually, we intersect.
                inter_start = max(rs_min, ns_min)
                inter_end = min(re_min, ne_min)
                
                if inter_start < inter_end:
                    # Valid overlap, update rule times
                    sh, sm = divmod(inter_start, 60)
                    eh, em = divmod(inter_end, 60)
                    
                    new_r_start = f"{sh:02d}:{sm:02d}"
                    new_r_end = f"{eh:02d}:{em:02d}"
                    
                    upd_stmt = text("UPDATE availability_rules SET start_time = :st, end_time = :et, updated_at = CURRENT_TIMESTAMP WHERE id = :rid")
                    db.execute(upd_stmt, {"st": new_r_start, "et": new_r_end, "rid": rule.id})
                else:
                    # No overlap, remove rule
                    rm_stmt = text("DELETE FROM availability_rules WHERE id = :rid")
                    db.execute(rm_stmt, {"rid": rule.id})

        # C. Update Schedule Timezone if necessary (though often tied to User/Default)
        # The prompt mentions America/Chicago specifically for the Sales Consultation task.
        # If the payload doesn't specify timezone change, we generally leave it alone unless implied by context.
        # However, looking at the initial data, Schedule 1 is already America/Chicago.
        # We won't force a timezone update here unless there's a field for it, which there isn't in the request body.
        
        db.commit()
        
        # 4. Construct Response
        final_rules_stmt = text("SELECT id, day_of_week, start_time, end_time FROM availability_rules WHERE schedule_id = :sid ORDER BY day_of_week, start_time")
        final_rules = db.execute(final_rules_stmt, {"sid": schedule_id}).fetchall()
        
        rules_list = [
            RuleResponse(
                id=r.id,
                day_of_week=r.day_of_week,
                start_time=r.start_time,
                end_time=r.end_time
            ) for r in final_rules
        ]
        
        updated_avail = UpdatedAvailabilityResponse(
            event_type_id=event_type_id,
            schedule_id=schedule_id,
            rules=rules_list
        )
        
        return AvailabilityUpdateResponse(
            message="Availability updated successfully",
            updated_availability=updated_avail
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/event_types/{event_type_id}/availability", response_model=GetAvailabilityResponse, operation_id="get_event_type_availability")
def get_event_type_availability(
    event_type_id: int = Path(..., description="The ID of the event type to retrieve availability for")
):
    """
    Retrieve the current availability schedule for a specific event type.
    """
    db = SessionLocal()
    try:
        # 1. Verify Event Type
        et_stmt = text("SELECT id FROM event_types WHERE id = :id")
        if not db.execute(et_stmt, {"id": event_type_id}).fetchone():
            raise HTTPException(status_code=404, detail="Event type not found")

        # 2. Get Linked Schedule
        eta_stmt = text("SELECT schedule_id FROM event_type_availability WHERE event_type_id = :et_id LIMIT 1")
        eta_row = db.execute(eta_stmt, {"et_id": event_type_id}).fetchone()
        
        if not eta_row:
            raise HTTPException(status_code=404, detail="No availability schedule linked to this event type")
            
        schedule_id = eta_row.schedule_id
        
        # 3. Get Rules
        rules_stmt = text("SELECT id, day_of_week, start_time, end_time FROM availability_rules WHERE schedule_id = :sid ORDER BY day_of_week, start_time")
        rows = db.execute(rules_stmt, {"sid": schedule_id}).fetchall()
        
        rules_list = [
            RuleResponse(
                id=r.id,
                day_of_week=r.day_of_week,
                start_time=r.start_time,
                end_time=r.end_time
            ) for r in rows
        ]
        
        return GetAvailabilityResponse(
            event_type_id=event_type_id,
            schedule_id=schedule_id,
            rules=rules_list
        )
        
    finally:
        db.close()
