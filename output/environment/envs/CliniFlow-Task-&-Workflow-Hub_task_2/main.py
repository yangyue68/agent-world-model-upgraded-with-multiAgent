import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ConditionItem(BaseModel):
    field_name: str
    operator: str
    field_value: str


class DuplicateProtocolRequest(BaseModel):
    new_name: str


class UpdateConditionsRequest(BaseModel):
    conditions: List[ConditionItem]


class UpdatePriorityRequest(BaseModel):
    priority: str


class CreateBranchRequest(BaseModel):
    branch_name: str
    conditions: List[ConditionItem]
    task_name: str
    assignee_role: str


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="No authenticated user")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")


@app.post("/protocols/{protocol_id}/duplicate", operation_id="duplicate_protocol")
def duplicate_protocol(
    protocol_id: int = Path(...),
    body: DuplicateProtocolRequest = None,
):
    current_user_id = get_current_user_id()

    with engine.connect() as conn:
        # Fetch the original protocol
        result = conn.execute(
            text("SELECT id, name, description, version, is_active, created_by FROM protocols WHERE id = :pid"),
            {"pid": protocol_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Protocol not found")

        orig_id, orig_name, orig_desc, orig_version, orig_is_active, orig_created_by = row

        # Insert a copy of the protocol with new name and incremented version
        new_version = orig_version + 1
        insert_result = conn.execute(
            text("""
                INSERT INTO protocols (name, description, version, is_active, created_by, created_at, updated_at)
                VALUES (:name, :desc, :version, :is_active, :created_by, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "name": body.new_name,
                "desc": orig_desc,
                "version": new_version,
                "is_active": orig_is_active,
                "created_by": current_user_id,
            },
        )
        new_protocol_id = insert_result.lastrowid

        # Copy conditions from original protocol
        cond_result = conn.execute(
            text("SELECT condition_group, field_name, operator, field_value, logical_operator FROM protocol_conditions WHERE protocol_id = :pid"),
            {"pid": orig_id},
        )
        for c_row in cond_result.fetchall():
            conn.execute(
                text("""
                    INSERT INTO protocol_conditions (protocol_id, condition_group, field_name, operator, field_value, logical_operator, created_at, updated_at)
                    VALUES (:pid, :cg, :fn, :op, :fv, :lo, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {
                    "pid": new_protocol_id,
                    "cg": c_row[0],
                    "fn": c_row[1],
                    "op": c_row[2],
                    "fv": c_row[3],
                    "lo": c_row[4],
                },
            )

        # Copy tasks from original protocol
        task_result = conn.execute(
            text("SELECT name, description, task_type, priority, assignee_role, assignee_user_id, due_offset_minutes, is_conditional, parent_task_id FROM protocol_tasks WHERE protocol_id = :pid"),
            {"pid": orig_id},
        )
        old_to_new_task_map = {}
        for t_row in task_result.fetchall():
            ins = conn.execute(
                text("""
                    INSERT INTO protocol_tasks (protocol_id, name, description, task_type, priority, assignee_role, assignee_user_id, due_offset_minutes, is_conditional, parent_task_id, created_at, updated_at)
                    VALUES (:pid, :name, :desc, :tt, :pr, :ar, :au, :dom, :ic, :pti, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {
                    "pid": new_protocol_id,
                    "name": t_row[0],
                    "desc": t_row[1],
                    "tt": t_row[2],
                    "pr": t_row[3],
                    "ar": t_row[4],
                    "au": t_row[5],
                    "dom": t_row[6],
                    "ic": t_row[7],
                    "pti": t_row[8],
                },
            )
            old_to_new_task_map[t_row[0]] = ins.lastrowid

        # Copy branches from original protocol
        branch_result = conn.execute(
            text("SELECT name, condition_group FROM protocol_branches WHERE protocol_id = :pid"),
            {"pid": orig_id},
        )
        old_to_new_branch_map = {}
        for b_row in branch_result.fetchall():
            ins = conn.execute(
                text("""
                    INSERT INTO protocol_branches (protocol_id, name, condition_group, created_at, updated_at)
                    VALUES (:pid, :name, :cg, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {
                    "pid": new_protocol_id,
                    "name": b_row[0],
                    "cg": b_row[1],
                },
            )
            old_to_new_branch_map[b_row[0]] = ins.lastrowid

        # Copy branch conditions
        bc_result = conn.execute(
            text("""
                SELECT pb.name, pbc.field_name, pbc.operator, pbc.field_value, pbc.logical_operator
                FROM protocol_branch_conditions pbc
                JOIN protocol_branches pb ON pbc.branch_id = pb.id
                WHERE pb.protocol_id = :pid
            """),
            {"pid": orig_id},
        )
        for bc_row in bc_result.fetchall():
            branch_name, fn, op, fv, lo = bc_row
            new_branch_id = old_to_new_branch_map.get(branch_name)
            if new_branch_id:
                conn.execute(
                    text("""
                        INSERT INTO protocol_branch_conditions (branch_id, field_name, operator, field_value, logical_operator, created_at, updated_at)
                        VALUES (:bid, :fn, :op, :fv, :lo, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """),
                    {
                        "bid": new_branch_id,
                        "fn": fn,
                        "op": op,
                        "fv": fv,
                        "lo": lo,
                    },
                )

        # Copy branch tasks
        bt_result = conn.execute(
            text("""
                SELECT pb.name, pt.name
                FROM protocol_branch_tasks pbt
                JOIN protocol_branches pb ON pbt.branch_id = pb.id
                JOIN protocol_tasks pt ON pbt.protocol_task_id = pt.id
                WHERE pb.protocol_id = :pid
            """),
            {"pid": orig_id},
        )
        for bt_row in bt_result.fetchall():
            branch_name, task_name = bt_row
            new_branch_id = old_to_new_branch_map.get(branch_name)
            new_task_id = old_to_new_task_map.get(task_name)
            if new_branch_id and new_task_id:
                conn.execute(
                    text("""
                        INSERT INTO protocol_branch_tasks (branch_id, protocol_task_id, created_at)
                        VALUES (:bid, :tid, CURRENT_TIMESTAMP)
                    """),
                    {
                        "bid": new_branch_id,
                        "tid": new_task_id,
                    },
                )

        # Audit log
        conn.execute(
            text("""
                INSERT INTO protocol_audit_log (protocol_id, user_id, action, details, created_at)
                VALUES (:pid, :uid, :action, :details, CURRENT_TIMESTAMP)
            """),
            {
                "pid": new_protocol_id,
                "uid": current_user_id,
                "action": "DUPLICATE",
                "details": f"Protocol '{orig_name}' duplicated as '{body.new_name}'.",
            },
        )

        conn.commit()

    return {"id": new_protocol_id, "name": body.new_name, "version": new_version}


@app.put("/protocols/{protocol_id}/conditions", operation_id="update_protocol_conditions")
def update_protocol_conditions(
    protocol_id: int = Path(...),
    body: UpdateConditionsRequest = None,
):
    current_user_id = get_current_user_id()

    with engine.connect() as conn:
        # Check protocol exists
        result = conn.execute(
            text("SELECT id FROM protocols WHERE id = :pid"),
            {"pid": protocol_id},
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Protocol not found")

        # Delete existing conditions for this protocol
        conn.execute(
            text("DELETE FROM protocol_conditions WHERE protocol_id = :pid"),
            {"pid": protocol_id},
        )

        # Insert new conditions
        for cond in body.conditions:
            conn.execute(
                text("""
                    INSERT INTO protocol_conditions (protocol_id, condition_group, field_name, operator, field_value, logical_operator, created_at, updated_at)
                    VALUES (:pid, 1, :fn, :op, :fv, 'AND', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {
                    "pid": protocol_id,
                    "fn": cond.field_name,
                    "op": cond.operator,
                    "fv": cond.field_value,
                },
            )

        # Audit log
        conn.execute(
            text("""
                INSERT INTO protocol_audit_log (protocol_id, user_id, action, details, created_at)
                VALUES (:pid, :uid, :action, :details, CURRENT_TIMESTAMP)
            """),
            {
                "pid": protocol_id,
                "uid": current_user_id,
                "action": "UPDATE_CONDITIONS",
                "details": f"Updated conditions for protocol {protocol_id}.",
            },
        )

        conn.commit()

    return {"message": "Conditions updated successfully"}


@app.put("/protocol_tasks/{task_id}/priority", operation_id="update_task_priority")
def update_task_priority(
    task_id: int = Path(...),
    body: UpdatePriorityRequest = None,
):
    current_user_id = get_current_user_id()

    valid_priorities = ["Low", "Medium", "High", "Critical"]
    if body.priority not in valid_priorities:
        raise HTTPException(status_code=400, detail=f"Invalid priority. Must be one of {valid_priorities}")

    with engine.connect() as conn:
        # Check task exists and get protocol_id
        result = conn.execute(
            text("SELECT id, protocol_id, name FROM protocol_tasks WHERE id = :tid"),
            {"tid": task_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")

        _, protocol_id, task_name = row

        # Update priority
        conn.execute(
            text("UPDATE protocol_tasks SET priority = :pr, updated_at = CURRENT_TIMESTAMP WHERE id = :tid"),
            {"pr": body.priority, "tid": task_id},
        )

        # Audit log
        conn.execute(
            text("""
                INSERT INTO protocol_audit_log (protocol_id, user_id, action, details, created_at)
                VALUES (:pid, :uid, :action, :details, CURRENT_TIMESTAMP)
            """),
            {
                "pid": protocol_id,
                "uid": current_user_id,
                "action": "UPDATE_TASK_PRIORITY",
                "details": f"Changed priority of task '{task_name}' to {body.priority}.",
            },
        )

        conn.commit()

    return {"message": "Task priority updated successfully"}


@app.post("/protocols/{protocol_id}/branches", operation_id="create_escalation_branch")
def create_escalation_branch(
    protocol_id: int = Path(...),
    body: CreateBranchRequest = None,
):
    current_user_id = get_current_user_id()

    with engine.connect() as conn:
        # Check protocol exists
        result = conn.execute(
            text("SELECT id FROM protocols WHERE id = :pid"),
            {"pid": protocol_id},
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Protocol not found")

        # Create branch
        branch_ins = conn.execute(
            text("""
                INSERT INTO protocol_branches (protocol_id, name, condition_group, created_at, updated_at)
                VALUES (:pid, :name, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "pid": protocol_id,
                "name": body.branch_name,
            },
        )
        branch_id = branch_ins.lastrowid

        # Add branch conditions
        for cond in body.conditions:
            conn.execute(
                text("""
                    INSERT INTO protocol_branch_conditions (branch_id, field_name, operator, field_value, logical_operator, created_at, updated_at)
                    VALUES (:bid, :fn, :op, :fv, 'AND', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {
                    "bid": branch_id,
                    "fn": cond.field_name,
                    "op": cond.operator,
                    "fv": cond.field_value,
                },
            )

        # Create escalation task
        task_ins = conn.execute(
            text("""
                INSERT INTO protocol_tasks (protocol_id, name, description, task_type, priority, assignee_role, due_offset_minutes, is_conditional, created_at, updated_at)
                VALUES (:pid, :name, :desc, 'escalation', 'High', :role, 120, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "pid": protocol_id,
                "name": body.task_name,
                "desc": f"Escalation task triggered by branch '{body.branch_name}'",
                "role": body.assignee_role,
            },
        )
        new_task_id = task_ins.lastrowid

        # Link task to branch
        conn.execute(
            text("""
                INSERT INTO protocol_branch_tasks (branch_id, protocol_task_id, created_at)
                VALUES (:bid, :tid, CURRENT_TIMESTAMP)
            """),
            {
                "bid": branch_id,
                "tid": new_task_id,
            },
        )

        # Audit log
        conn.execute(
            text("""
                INSERT INTO protocol_audit_log (protocol_id, user_id, action, details, created_at)
                VALUES (:pid, :uid, :action, :details, CURRENT_TIMESTAMP)
            """),
            {
                "pid": protocol_id,
                "uid": current_user_id,
                "action": "CREATE_BRANCH",
                "details": f"Added branch '{body.branch_name}' with escalation task '{body.task_name}'.",
            },
        )

        conn.commit()

    return {"message": "Branch created successfully"}
