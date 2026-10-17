import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CVS Pharmacy Appointment Service")


class RescheduleRequest(BaseModel):
    new_datetime: str


class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    provider_id: int
    location_id: int
    appointment_datetime: str
    duration_minutes: int
    reason: str
    status: str


@app.put("/appointments/{appointment_id}", response_model=AppointmentResponse, operation_id="reschedule_appointment")
def reschedule_appointment(
    appointment_id: int = Path(..., description="Internal ID of the appointment to reschedule"),
    body: RescheduleRequest = ...
):
    """
    Update the datetime of an existing appointment to a new date and time. 
    The appointment must be currently scheduled and belong to the authenticated user.
    """
    
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    try:
        with engine.connect() as conn:
            # 1. Verify the appointment exists, belongs to the current user (via patient), and is 'scheduled'
            query_check = text("""
                SELECT a.id, a.patient_id, a.provider_id, a.location_id, a.appointment_datetime, a.duration_minutes, a.reason, a.status
                FROM appointments a
                JOIN patients p ON a.patient_id = p.id
                WHERE a.id = :appointment_id
                  AND p.user_id = :user_id
                  AND a.status = 'scheduled'
            """)
            
            result = conn.execute(query_check, {
                "appointment_id": appointment_id,
                "user_id": current_user_id
            })
            
            row = result.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="Appointment not found or does not belong to user/is not scheduled")

            # Extract data for potential return before update, though we will re-fetch after update to ensure consistency
            # But since we are updating only datetime, other fields remain same.
            
            # 2. Perform the update
            # Note: The spec says "Update the datetime". It doesn't explicitly say change location/provider unless specified in task.
            # Task says: "...at the CVS Pharmacy located at 789 Pine Road, Austin, TX."
            # Looking at initial snapshot: Location ID 1 is "CVS Pharmacy - Pine Road", "789 Pine Road", "Austin", "TX".
            # The original appointment (ID 1) has location_id 1. So the location remains the same.
            # We just need to update appointment_datetime.
            
            update_query = text("""
                UPDATE appointments
                SET appointment_datetime = :new_datetime,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :appointment_id
            """)
            
            conn.execute(update_query, {
                "new_datetime": body.new_datetime,
                "appointment_id": appointment_id
            })
            
            conn.commit()
            
            # 3. Fetch the updated record to return
            final_query = text("""
                SELECT id, patient_id, provider_id, location_id, appointment_datetime, duration_minutes, reason, status
                FROM appointments
                WHERE id = :appointment_id
            """)
            
            final_result = conn.execute(final_query, {"appointment_id": appointment_id})
            updated_row = final_result.fetchone()
            
            if not updated_row:
                 # Should theoretically not happen if commit succeeded
                 raise HTTPException(status_code=500, detail="Failed to retrieve updated appointment")

            return AppointmentResponse(
                id=updated_row[0],
                patient_id=updated_row[1],
                provider_id=updated_row[2],
                location_id=updated_row[3],
                appointment_datetime=str(updated_row[4]),
                duration_minutes=updated_row[5],
                reason=updated_row[6],
                status=updated_row[7]
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
