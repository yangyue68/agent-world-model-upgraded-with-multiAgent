import os
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AssignTaskRequest(BaseModel):
    assigned_to: int
    assigned_by: int
    description: str | None = None


class TaskAlertResponse(BaseModel):
    alert_id: int


class AssignTaskResponse(BaseModel):
    task_id: int
    alert_id: int


@app.post("/care_plans/{plan_code}/tasks", response_model=AssignTaskResponse, operation_id="assign_followup_task")
def assign_followup_task(plan_code: str, body: AssignTaskRequest):
    with engine.begin() as conn:
        # Validate care plan exists and get its ID
        cp_row = conn.execute(
            text("SELECT id FROM care_plans WHERE plan_code = :plan_code"),
            {"plan_code": plan_code}
        ).fetchone()
        
        if not cp_row:
            raise HTTPException(status_code=404, detail=f"Care plan '{plan_code}' not found")
        
        care_plan_id = cp_row[0]
        
        # Calculate due date (48 hours from now)
        now = datetime.utcnow()
        due_at = now + timedelta(hours=48)
        due_at_str = due_at.strftime("%Y-%m-%d %H:%M:%S")
        created_at_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        # Insert new task
        result = conn.execute(
            text("""
                INSERT INTO tasks (care_plan_id, assigned_to, assigned_by, task_type, description, due_at, status, created_at, updated_at)
                VALUES (:care_plan_id, :assigned_to, :assigned_by, :task_type, :description, :due_at, :status, :created_at, :updated_at)
            """),
            {
                "care_plan_id": care_plan_id,
                "assigned_to": body.assigned_to,
                "assigned_by": body.assigned_by,
                "task_type": "medication_reconciliation",
                "description": body.description or "Conduct medication reconciliation visit within 48 hours",
                "due_at": due_at_str,
                "status": "pending",
                "created_at": created_at_str,
                "updated_at": created_at_str,
            }
        )
        task_id = result.lastrowid
        
        # Create automated overdue alert
        alert_result = conn.execute(
            text("""
                INSERT INTO alerts (task_id, alert_type, trigger_at, status, created_at, updated_at)
                VALUES (:task_id, :alert_type, :trigger_at, :status, :created_at, :updated_at)
            """),
            {
                "task_id": task_id,
                "alert_type": "task_overdue",
                "trigger_at": due_at_str,
                "status": "scheduled",
                "created_at": created_at_str,
                "updated_at": created_at_str,
            }
        )
        alert_id = alert_result.lastrowid
    
    return AssignTaskResponse(task_id=task_id, alert_id=alert_id)


@app.post("/tasks/{task_id}/alerts", response_model=TaskAlertResponse, operation_id="create_task_alert")
def create_task_alert(task_id: int):
    with engine.begin() as conn:
        # Get task details to find due_at
        task_row = conn.execute(
            text("SELECT due_at FROM tasks WHERE id = :task_id"),
            {"task_id": task_id}
        ).fetchone()
        
        if not task_row:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        
        due_at = task_row[0]
        now = datetime.utcnow()
        created_at_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        # Insert alert
        result = conn.execute(
            text("""
                INSERT INTO alerts (task_id, alert_type, trigger_at, status, created_at, updated_at)
                VALUES (:task_id, :alert_type, :trigger_at, :status, :created_at, :updated_at)
            """),
            {
                "task_id": task_id,
                "alert_type": "task_overdue",
                "trigger_at": due_at,
                "status": "scheduled",
                "created_at": created_at_str,
                "updated_at": created_at_str,
            }
        )
        alert_id = result.lastrowid
    
    return TaskAlertResponse(alert_id=alert_id)
