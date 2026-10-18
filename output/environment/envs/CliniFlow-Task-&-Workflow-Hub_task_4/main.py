import os
from datetime import datetime, timezone
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

app = FastAPI(title="CliniFlow Task & Workflow Hub")


class ReassignTaskRequest(BaseModel):
    assigned_to_user_id: int = Field(..., description="Internal ID of the new assignee (must be an eligibility specialist)")
    escalation_reason: str = Field(..., description="Reason for escalation (e.g., 'Pending 48-hour SLA breach')")
    new_due_date: str = Field(..., description="New due date in ISO 8601 format (e.g., 2024-05-22T17:00:00Z)")


class ReassignTaskResponse(BaseModel):
    task_id: int
    assigned_to_user_id: int
    escalation_reason: str
    new_due_date: str


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO 8601 string handling both Z suffix and +00:00 offset."""
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Fallback for space-separated formats if any
        if ' ' in dt_str:
            parts = dt_str.split(' ')
            if len(parts) == 2:
                combined = f"{parts[0]}T{parts[1]}"
                if combined.endswith('Z'):
                    combined = combined[:-1] + '+00:00'
                return datetime.fromisoformat(combined)
        raise


@app.put("/tasks/{task_id}/reassign", response_model=ReassignTaskResponse, operation_id="reassign_task")
async def reassign_task(
    task_id: int = Path(..., description="Internal ID of the task to reassign"),
    payload: ReassignTaskRequest = Body(...)
):
    current_user_id_env = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_env:
        raise HTTPException(status_code=401, detail="Current user context missing")
    
    try:
        current_user_id = int(current_user_id_env)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")

    db = SessionLocal()
    try:
        # 1. Verify the task exists and get current details
        stmt_task = text("""
            SELECT id, title, assigned_to_user_id, status 
            FROM tasks 
            WHERE id = :tid
        """)
        res_task = db.execute(stmt_task, {"tid": task_id})
        task_row = res_task.fetchone()
        
        if not task_row:
            raise HTTPException(status_code=404, detail="Task not found")
        
        current_assigned_to_user_id = task_row[2]
        task_status = task_row[3]

        # Spec says: "The task must be currently assigned to a provider"
        # We need to check the role of the current assignee
        stmt_current_user_role = text("""
            SELECT role FROM users WHERE id = :uid
        """)
        res_curr_role = db.execute(stmt_current_user_role, {"uid": current_assigned_to_user_id})
        curr_role_row = res_curr_role.fetchone()
        
        if not curr_role_row:
             raise HTTPException(status_code=400, detail="Current assignee does not exist")
             
        if curr_role_row[0] != 'provider':
            raise HTTPException(status_code=400, detail="Task must be currently assigned to a provider to be reassigned via this endpoint")

        # 2. Verify the new assignee exists and is an eligibility specialist
        stmt_new_user = text("""
            SELECT id, role FROM users WHERE id = :uid
        """)
        res_new_user = db.execute(stmt_new_user, {"uid": payload.assigned_to_user_id})
        new_user_row = res_new_user.fetchone()
        
        if not new_user_row:
            raise HTTPException(status_code=400, detail="New assignee does not exist")
            
        if new_user_row[1] != 'eligibility_specialist':
            raise HTTPException(status_code=400, detail="New assignee must be an eligibility specialist")

        # 3. Parse the new due date
        try:
            parsed_due_date = parse_iso_datetime(payload.new_due_date)
            # Store as naive UTC or standard DATETIME format expected by SQLite/SQLAlchemy usually depends on config.
            # The schema uses DATETIME. Let's convert to string format compatible with DB storage if needed, 
            # but SQLAlchemy handles datetime objects well.
            # However, looking at initial snapshot, dates are stored like "2024-05-20T17:00:00Z".
            # To ensure consistency with how data might be read back or compared, we should store it properly.
            # Since we are using raw SQL updates below, let's format it correctly.
            # Standard ISO format without Z for internal storage often works, but let's stick to what input was provided or normalize.
            # The prompt asks to set new due date to 2024-05-22T17:00:00Z.
            # We will pass the datetime object to SQLAlchemy which maps it.
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")

        # 4. Update the task record
        stmt_update_task = text("""
            UPDATE tasks 
            SET assigned_to_user_id = :new_uid, 
                due_date = :new_due, 
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :tid
        """)
        db.execute(stmt_update_task, {
            "new_uid": payload.assigned_to_user_id,
            "new_due": parsed_due_date,
            "tid": task_id
        })

        # 5. Insert into task_assignments log
        stmt_insert_assignment = text("""
            INSERT INTO task_assignments (task_id, assigned_from_user_id, assigned_to_user_id, reason, assigned_at)
            VALUES (:tid, :from_uid, :to_uid, :reason, CURRENT_TIMESTAMP)
        """)
        db.execute(stmt_insert_assignment, {
            "tid": task_id,
            "from_uid": current_assigned_to_user_id,
            "to_uid": payload.assigned_to_user_id,
            "reason": payload.escalation_reason
        })

        # 6. Insert into task_escalations log
        stmt_insert_escalation = text("""
            INSERT INTO task_escalations (task_id, escalated_by_user_id, reason, escalated_at)
            VALUES (:tid, :actor_uid, :reason, CURRENT_TIMESTAMP)
        """)
        db.execute(stmt_insert_escalation, {
            "tid": task_id,
            "actor_uid": current_user_id,
            "reason": payload.escalation_reason
        })

        db.commit()

        # Return response matching the requested structure
        # Note: The response requires returning the new_due_date as string.
        # We'll return the original input string for fidelity unless normalization is strictly required.
        return ReassignTaskResponse(
            task_id=task_id,
            assigned_to_user_id=payload.assigned_to_user_id,
            escalation_reason=payload.escalation_reason,
            new_due_date=payload.new_due_date
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
