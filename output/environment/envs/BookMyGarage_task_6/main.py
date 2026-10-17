import os
from datetime import datetime, date, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AvailableSlot(BaseModel):
    start_time: str
    end_time: str
    bay_id: int


class RescheduleResponse(BaseModel):
    appointment_id: str
    shop_id: int
    service_package_id: int
    appointment_date: str
    start_time: str
    end_time: str
    status: str


def get_next_available_slot(shop_id: int, target_date: str, duration_minutes: int) -> Optional[AvailableSlot]:
    """
    Find the next available hourly slot for a given shop and date that can accommodate
    the specified duration. Returns None if no such slot exists.
    
    The logic checks bay_availability table for slots marked as available (is_available=1)
    on the given date for bays belonging to the shop. It then verifies that consecutive
    hourly slots are available to cover the required duration.
    """
    with engine.connect() as conn:
        # Get all active bays for the shop
        bays_result = conn.execute(
            text("""
                SELECT id FROM shop_bays 
                WHERE shop_id = :shop_id AND is_active = 1
            """),
            {"shop_id": shop_id}
        )
        bay_ids = [row[0] for row in bays_result.fetchall()]
        
        if not bay_ids:
            return None
        
        # Get all available time slots for these bays on the target date
        placeholders = ", ".join([f":bay_{i}" for i in range(len(bay_ids))])
        params = {f"bay_{i}": bay_id for i, bay_id in enumerate(bay_ids)}
        params["target_date"] = target_date
        
        availability_result = conn.execute(
            text(f"""
                SELECT bay_id, start_time, end_time 
                FROM bay_availability 
                WHERE bay_id IN ({placeholders}) 
                  AND available_date = :target_date 
                  AND is_available = 1
                ORDER BY bay_id, start_time
            """),
            params
        )
        
        rows = availability_result.fetchall()
        
        # Group by bay_id
        from collections import defaultdict
        bay_slots = defaultdict(list)
        for row in rows:
            bay_slots[row[0]].append((row[1], row[2]))
        
        # For each bay, find consecutive slots that can cover the duration
        # Slots are hourly, so we need ceil(duration_minutes / 60) consecutive slots
        num_slots_needed = (duration_minutes + 59) // 60  # Ceiling division
        
        for bay_id, slots in bay_slots.items():
            if len(slots) < num_slots_needed:
                continue
            
            # Sort slots by start time
            sorted_slots = sorted(slots, key=lambda x: x[0])
            
            # Check for consecutive hourly slots
            for i in range(len(sorted_slots) - num_slots_needed + 1):
                current_group = sorted_slots[i:i + num_slots_needed]
                
                # Verify they are consecutive hours
                valid = True
                for j in range(len(current_group) - 1):
                    end_time_j = current_group[j][1]
                    start_time_next = current_group[j + 1][0]
                    
                    # Parse times to check continuity
                    try:
                        h1, m1 = map(int, end_time_j.split(":"))
                        h2, m2 = map(int, start_time_next.split(":"))
                        
                        # End of one slot should equal start of next (hourly alignment)
                        if h1 != h2 or m1 != 0 or m2 != 0:
                            valid = False
                            break
                    except ValueError:
                        valid = False
                        break
                
                if valid:
                    first_start = current_group[0][0]
                    last_end = current_group[-1][1]
                    return AvailableSlot(
                        start_time=first_start,
                        end_time=last_end,
                        bay_id=bay_id
                    )
        
        return None


@app.put("/appointments/{appointment_id}/reschedule", response_model=RescheduleResponse, operation_id="reschedule_appointment")
def reschedule_appointment(
    appointment_id: str,
    new_date: Optional[str] = Query(None, description="Optional new date for the appointment (YYYY-MM-DD). If not provided, uses today's date.")
):
    """
    Reschedule an appointment to the next available slot at the same shop, preserving the same service package.
    Ensures bay availability and real-time slot alignment.
    """
    with engine.connect() as conn:
        # Fetch the existing appointment
        result = conn.execute(
            text("""
                SELECT id, user_id, shop_id, bay_id, service_package_id, appointment_date, start_time, end_time, status
                FROM appointments
                WHERE id = :appointment_id
            """),
            {"appointment_id": appointment_id}
        )
        appt_row = result.fetchone()
        
        if not appt_row:
            raise HTTPException(status_code=404, detail="Appointment not found")
        
        appt_id, user_id, shop_id, old_bay_id, service_package_id, old_date, old_start, old_end, status = appt_row
        
        if status != "scheduled":
            raise HTTPException(status_code=400, detail="Only scheduled appointments can be rescheduled")
        
        # Determine the target date
        if new_date:
            try:
                datetime.strptime(new_date, "%Y-%m-%d")
                target_date = new_date
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        else:
            target_date = date.today().isoformat()
        
        # Get service package duration
        pkg_result = conn.execute(
            text("""
                SELECT duration_minutes FROM service_packages WHERE id = :pkg_id
            """),
            {"pkg_id": service_package_id}
        )
        pkg_row = pkg_result.fetchone()
        if not pkg_row:
            raise HTTPException(status_code=404, detail="Service package not found")
        duration_minutes = pkg_row[0]
        
        # Find the next available slot
        available_slot = get_next_available_slot(shop_id, target_date, duration_minutes)
        
        if not available_slot:
            raise HTTPException(status_code=404, detail="No available slots found for the requested date and service duration")
        
        # Update the appointment
        updated_at = datetime.now().isoformat()
        conn.execute(
            text("""
                UPDATE appointments
                SET appointment_date = :new_date,
                    start_time = :new_start,
                    end_time = :new_end,
                    bay_id = :new_bay_id,
                    updated_at = :updated_at
                WHERE id = :appointment_id
            """),
            {
                "new_date": target_date,
                "new_start": available_slot.start_time,
                "new_end": available_slot.end_time,
                "new_bay_id": available_slot.bay_id,
                "updated_at": updated_at,
                "appointment_id": appointment_id
            }
        )
        
        # Mark the old bay availability as unavailable if it was previously available
        # And mark the new bay availability as unavailable
        # Actually, the bay_availability table represents pre-defined availability windows.
        # When an appointment is booked/rescheduled, we should update the corresponding bay_availability record
        # to reflect that the slot is now taken.
        
        # First, restore the old slot's availability if it existed
        if old_bay_id:
            conn.execute(
                text("""
                    UPDATE bay_availability
                    SET is_available = 1, updated_at = :updated_at
                    WHERE bay_id = :old_bay_id
                      AND available_date = :old_date
                      AND start_time = :old_start
                """),
                {
                    "old_bay_id": old_bay_id,
                    "old_date": old_date,
                    "old_start": old_start,
                    "updated_at": updated_at
                }
            )
        
        # Then, mark the new slot as unavailable
        conn.execute(
            text("""
                UPDATE bay_availability
                SET is_available = 0, updated_at = :updated_at
                WHERE bay_id = :new_bay_id
                  AND available_date = :new_date
                  AND start_time = :new_start
            """),
            {
                "new_bay_id": available_slot.bay_id,
                "new_date": target_date,
                "new_start": available_slot.start_time,
                "updated_at": updated_at
            }
        )
        
        conn.commit()
        
        return RescheduleResponse(
            appointment_id=appt_id,
            shop_id=shop_id,
            service_package_id=service_package_id,
            appointment_date=target_date,
            start_time=available_slot.start_time,
            end_time=available_slot.end_time,
            status=status
        )


@app.get("/shops/{shop_id}/available_slots", response_model=List[AvailableSlot], operation_id="get_available_slots")
def get_available_slots(
    shop_id: int,
    date: str = Query(..., description="Date to check availability (YYYY-MM-DD)")
):
    """
    Retrieve all available hourly time slots for a given shop on a specified date, considering bay availability and service package duration.
    """
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    with engine.connect() as conn:
        # Verify shop exists
        shop_result = conn.execute(
            text("SELECT id FROM shops WHERE id = :shop_id"),
            {"shop_id": shop_id}
        )
        if not shop_result.fetchone():
            raise HTTPException(status_code=404, detail="Shop not found")
        
        # Get all active bays for the shop
        bays_result = conn.execute(
            text("""
                SELECT id FROM shop_bays 
                WHERE shop_id = :shop_id AND is_active = 1
            """),
            {"shop_id": shop_id}
        )
        bay_ids = [row[0] for row in bays_result.fetchall()]
        
        if not bay_ids:
            return []
        
        # Get all available time slots for these bays on the given date
        placeholders = ", ".join([f":bay_{i}" for i in range(len(bay_ids))])
        params = {f"bay_{i}": bay_id for i, bay_id in enumerate(bay_ids)}
        params["target_date"] = date
        
        availability_result = conn.execute(
            text(f"""
                SELECT bay_id, start_time, end_time 
                FROM bay_availability 
                WHERE bay_id IN ({placeholders}) 
                  AND available_date = :target_date 
                  AND is_available = 1
                ORDER BY bay_id, start_time
            """),
            params
        )
        
        rows = availability_result.fetchall()
        
        slots = [
            AvailableSlot(
                start_time=row[1],
                end_time=row[2],
                bay_id=row[0]
            )
            for row in rows
        ]
        
        return slots
