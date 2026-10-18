import os
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CliniFlow Task & Workflow Hub")


class TaskItem(BaseModel):
    sequence_order: int
    name: str
    description: Optional[str] = None
    assignee_role: Optional[str] = None
    assignee_user_id: Optional[int] = None
    due_offset_hours: Optional[int] = None
    notification_channel: Optional[str] = None
    notification_provider: Optional[str] = None
    notification_offset_hours: Optional[int] = None


class ProtocolCreateRequest(BaseModel):
    name: str
    version: str
    department_id: int
    trigger_event: str
    trigger_icd10_pattern: Optional[str] = None
    tasks: List[TaskItem]


class ProtocolResponse(BaseModel):
    id: int
    name: str
    version: str
    department_id: int
    trigger_event: str
    trigger_icd10_pattern: Optional[str] = None
    created_at: str


@app.post("/protocols", response_model=ProtocolResponse, operation_id="create_clinical_workflow_protocol")
def create_clinical_workflow_protocol(req: ProtocolCreateRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    # Validate department exists
    with engine.connect() as conn:
        dept_result = conn.execute(
            text("SELECT id FROM departments WHERE id = :dept_id"),
            {"dept_id": req.department_id}
        ).fetchone()
        if not dept_result:
            raise HTTPException(status_code=404, detail="Department not found")

        # Validate trigger_event against CHECK constraint
        valid_trigger_events = {'patient_registration', 'manual', 'scheduled'}
        if req.trigger_event not in valid_trigger_events:
            raise HTTPException(status_code=422, detail=f"trigger_event must be one of {valid_trigger_events}")

        # Insert protocol
        insert_protocol_sql = text("""
            INSERT INTO protocols (name, version, department_id, trigger_event, trigger_icd10_pattern, is_active, created_by)
            VALUES (:name, :version, :department_id, :trigger_event, :trigger_icd10_pattern, 1, :created_by)
        """)
        result = conn.execute(insert_protocol_sql, {
            "name": req.name,
            "version": req.version,
            "department_id": req.department_id,
            "trigger_event": req.trigger_event,
            "trigger_icd10_pattern": req.trigger_icd10_pattern,
            "created_by": current_user_id
        })
        protocol_id = result.lastrowid

        # Fetch created_at for the new protocol
        proto_row = conn.execute(
            text("SELECT created_at FROM protocols WHERE id = :pid"),
            {"pid": protocol_id}
        ).fetchone()
        created_at_val = proto_row[0] if proto_row else None

        # Insert tasks
        for task in req.tasks:
            # Validate notification_channel if provided
            if task.notification_channel is not None:
                valid_channels = {'sms', 'email', 'none'}
                if task.notification_channel not in valid_channels:
                    raise HTTPException(status_code=422, detail=f"notification_channel must be one of {valid_channels}")

            # Validate due_offset_hours >= 0 if provided
            if task.due_offset_hours is not None and task.due_offset_hours < 0:
                raise HTTPException(status_code=422, detail="due_offset_hours must be >= 0")

            # Validate notification_offset_hours >= 0 if provided
            if task.notification_offset_hours is not None and task.notification_offset_hours < 0:
                raise HTTPException(status_code=422, detail="notification_offset_hours must be >= 0")

            insert_task_sql = text("""
                INSERT INTO protocol_tasks (protocol_id, sequence_order, name, description, assignee_role, assignee_user_id, due_offset_hours, notification_channel, notification_provider, notification_offset_hours)
                VALUES (:protocol_id, :sequence_order, :name, :description, :assignee_role, :assignee_user_id, :due_offset_hours, :notification_channel, :notification_provider, :notification_offset_hours)
            """)
            conn.execute(insert_task_sql, {
                "protocol_id": protocol_id,
                "sequence_order": task.sequence_order,
                "name": task.name,
                "description": task.description,
                "assignee_role": task.assignee_role,
                "assignee_user_id": task.assignee_user_id,
                "due_offset_hours": task.due_offset_hours,
                "notification_channel": task.notification_channel,
                "notification_provider": task.notification_provider,
                "notification_offset_hours": task.notification_offset_hours
            })

        conn.commit()

    return ProtocolResponse(
        id=protocol_id,
        name=req.name,
        version=req.version,
        department_id=req.department_id,
        trigger_event=req.trigger_event,
        trigger_icd10_pattern=req.trigger_icd10_pattern,
        created_at=str(created_at_val)
    )
