import os
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Assume DATABASE_PATH is provided in the environment or context as per instructions
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AdjustScheduleRequest(BaseModel):
    days_shifted: int = Field(..., description="Number of days to shift the schedule forward (positive integer)")
    reason: str = Field(..., description="Reason for the adjustment")
    approved_by: str = Field(..., description="Name or identifier of approver")


class EscalationRuleResponse(BaseModel):
    id: int
    days_before_due: int
    escalation_level: int
    notify_user_id: Optional[int] = None
    message_template: str
    is_active: bool


class MaintenanceScheduleDetailsResponse(BaseModel):
    schedule_id: int
    equipment_id: int
    schedule_type: str
    description: Optional[str] = None
    interval_days: Optional[int] = None
    next_due_date: str  # ISO format date string
    last_completed_date: Optional[str] = None  # ISO format date string
    is_active: bool
    escalation_rules: list[EscalationRuleResponse]


class AdjustScheduleResponse(BaseModel):
    success: bool
    message: str
    schedule_id: int
    previous_due_date: str  # ISO format date string
    new_due_date: str  # ISO format date string


def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()


@app.put(
    "/equipment/{asset_tag}/maintenance-schedules/{schedule_type}/adjust",
    response_model=AdjustScheduleResponse,
    operation_id="adjust_maintenance_schedule"
)
def adjust_maintenance_schedule(
    asset_tag: str = Path(..., description="Unique identifier for the equipment"),
    schedule_type: str = Path(..., description="Type of maintenance schedule"),
    request: AdjustScheduleRequest = Body(...)
):
    if request.days_shifted <= 0:
        raise HTTPException(status_code=400, detail="days_shifted must be a positive integer")

    with Session(engine) as session:
        # Find equipment by asset_tag
        eq_result = session.execute(
            text("SELECT id FROM equipment WHERE asset_tag = :asset_tag"),
            {"asset_tag": asset_tag}
        ).fetchone()
        
        if not eq_result:
            raise HTTPException(status_code=404, detail=f"Equipment with asset_tag '{asset_tag}' not found")
        
        equipment_id = eq_result[0]

        # Find maintenance schedule by equipment_id and schedule_type
        sched_result = session.execute(
            text("""
                SELECT id, next_due_date 
                FROM maintenance_schedules 
                WHERE equipment_id = :equipment_id AND schedule_type = :schedule_type
            """),
            {"equipment_id": equipment_id, "schedule_type": schedule_type}
        ).fetchone()

        if not sched_result:
            raise HTTPException(status_code=404, detail=f"Maintenance schedule of type '{schedule_type}' not found for equipment '{asset_tag}'")

        schedule_id = sched_result[0]
        current_next_due_date_str = sched_result[1]
        
        # Parse current due date
        try:
            current_next_due_date = date.fromisoformat(current_next_due_date_str)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid date format in database")

        # Calculate new due date
        new_due_date = current_next_due_date + timedelta(days=request.days_shifted)
        new_due_date_str = new_due_date.isoformat()

        # Update the maintenance schedule
        session.execute(
            text("""
                UPDATE maintenance_schedules 
                SET next_due_date = :new_due_date, updated_at = CURRENT_TIMESTAMP 
                WHERE id = :schedule_id
            """),
            {"new_due_date": new_due_date_str, "schedule_id": schedule_id}
        )

        # Get current user ID from environment variable
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id_str:
            raise HTTPException(status_code=401, detail="Missing AWM_CURRENT_USER_ID environment variable")
        
        try:
            adjusted_by_user_id = int(current_user_id_str)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid AWM_CURRENT_USER_ID format")

        # Insert into schedule_adjustments table
        session.execute(
            text("""
                INSERT INTO schedule_adjustments 
                (schedule_id, adjusted_by_user_id, adjustment_type, days_shifted, previous_due_date, new_due_date, reason, approved_by)
                VALUES 
                (:schedule_id, :adjusted_by_user_id, 'shift_forward', :days_shifted, :previous_due_date, :new_due_date, :reason, :approved_by)
            """),
            {
                "schedule_id": schedule_id,
                "adjusted_by_user_id": adjusted_by_user_id,
                "days_shifted": request.days_shifted,
                "previous_due_date": current_next_due_date_str,
                "new_due_date": new_due_date_str,
                "reason": request.reason,
                "approved_by": request.approved_by
            }
        )

        # Commit transaction
        session.commit()

        return AdjustScheduleResponse(
            success=True,
            message="Maintenance schedule adjusted successfully",
            schedule_id=schedule_id,
            previous_due_date=current_next_due_date_str,
            new_due_date=new_due_date_str
        )


@app.get(
    "/equipment/{asset_tag}/maintenance-schedules/{schedule_type}",
    response_model=MaintenanceScheduleDetailsResponse,
    operation_id="get_maintenance_schedule_details"
)
def get_maintenance_schedule_details(
    asset_tag: str = Path(..., description="Unique identifier for the equipment"),
    schedule_type: str = Path(..., description="Type of maintenance schedule")
):
    with Session(engine) as session:
        # Find equipment by asset_tag
        eq_result = session.execute(
            text("SELECT id FROM equipment WHERE asset_tag = :asset_tag"),
            {"asset_tag": asset_tag}
        ).fetchone()
        
        if not eq_result:
            raise HTTPException(status_code=404, detail=f"Equipment with asset_tag '{asset_tag}' not found")
        
        equipment_id = eq_result[0]

        # Find maintenance schedule
        sched_result = session.execute(
            text("""
                SELECT id, equipment_id, schedule_type, description, interval_days, 
                       next_due_date, last_completed_date, is_active 
                FROM maintenance_schedules 
                WHERE equipment_id = :equipment_id AND schedule_type = :schedule_type
            """),
            {"equipment_id": equipment_id, "schedule_type": schedule_type}
        ).fetchone()

        if not sched_result:
            raise HTTPException(status_code=404, detail=f"Maintenance schedule of type '{schedule_type}' not found for equipment '{asset_tag}'")

        schedule_id = sched_result[0]
        eq_id = sched_result[1]
        s_type = sched_result[2]
        desc = sched_result[3]
        interval = sched_result[4]
        next_due = sched_result[5]
        last_completed = sched_result[6]
        is_active = bool(sched_result[7])

        # Fetch escalation rules for this schedule
        esc_results = session.execute(
            text("""
                SELECT id, days_before_due, escalation_level, notify_user_id, message_template, is_active 
                FROM escalation_rules 
                WHERE schedule_id = :schedule_id
            """),
            {"schedule_id": schedule_id}
        ).fetchall()

        escalation_rules = []
        for row in esc_results:
            escalation_rules.append(EscalationRuleResponse(
                id=row[0],
                days_before_due=row[1],
                escalation_level=row[2],
                notify_user_id=row[3],
                message_template=row[4],
                is_active=bool(row[5])
            ))

        return MaintenanceScheduleDetailsResponse(
            schedule_id=schedule_id,
            equipment_id=eq_id,
            schedule_type=s_type,
            description=desc,
            interval_days=interval,
            next_due_date=next_due,
            last_completed_date=last_completed,
            is_active=is_active,
            escalation_rules=escalation_rules
        )
