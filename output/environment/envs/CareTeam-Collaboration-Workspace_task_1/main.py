import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

app = FastAPI()


# --- Pydantic Models for Request/Response ---

class UpdateCarePlanRequest(BaseModel):
    goal_3_description: str
    new_intervention: str


class UpdateCarePlanResponse(BaseModel):
    care_plan_id: str
    updated_goal_3: str
    added_intervention: str


class CreateFollowUpTaskRequest(BaseModel):
    care_plan_id: str
    assigned_to_user_id: int
    description: str
    trigger_text: str


class CreateFollowUpTaskResponse(BaseModel):
    task_id: int
    care_plan_id: str
    assigned_to_user_id: int
    description: str
    trigger_text: str


class LogContactNoteRequest(BaseModel):
    care_plan_id: str
    author_user_id: int
    note_text: str
    transcript: str


class LogContactNoteResponse(BaseModel):
    note_id: int
    care_plan_id: str
    author_user_id: int
    note_text: str
    transcript: str


# --- Endpoints ---

@app.put("/care_plans/{care_plan_id}", response_model=UpdateCarePlanResponse, operation_id="update_care_plan")
def update_care_plan(care_plan_id: str, body: UpdateCarePlanRequest):
    db = SessionLocal()
    try:
        # Verify care plan exists
        cp_check = db.execute(text("SELECT id FROM care_plans WHERE id = :cp_id"), {"cp_id": care_plan_id}).fetchone()
        if not cp_check:
            raise HTTPException(status_code=404, detail="Care plan not found")

        # Update Goal 3
        result_update_goal = db.execute(
            text("""
                UPDATE care_plan_goals 
                SET description = :new_desc, updated_at = CURRENT_TIMESTAMP 
                WHERE care_plan_id = :cp_id AND goal_number = 3
            """),
            {"new_desc": body.goal_3_description, "cp_id": care_plan_id}
        )
        
        if result_update_goal.rowcount == 0:
            raise HTTPException(status_code=404, detail="Goal 3 not found for this care plan")

        # Add New Intervention
        db.execute(
            text("""
                INSERT INTO care_plan_interventions (care_plan_id, description, created_at, updated_at)
                VALUES (:cp_id, :desc, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {"cp_id": care_plan_id, "desc": body.new_intervention}
        )

        db.commit()

        return UpdateCarePlanResponse(
            care_plan_id=care_plan_id,
            updated_goal_3=body.goal_3_description,
            added_intervention=body.new_intervention
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/care_plan_tasks", response_model=CreateFollowUpTaskResponse, operation_id="create_follow_up_task")
def create_follow_up_task(body: CreateFollowUpTaskRequest):
    db = SessionLocal()
    try:
        # Verify care plan exists
        cp_check = db.execute(text("SELECT id FROM care_plans WHERE id = :cp_id"), {"cp_id": body.care_plan_id}).fetchone()
        if not cp_check:
            raise HTTPException(status_code=404, detail="Care plan not found")

        # Verify user exists
        user_check = db.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": body.assigned_to_user_id}).fetchone()
        if not user_check:
            raise HTTPException(status_code=404, detail="User not found")

        # Insert Task
        result_insert = db.execute(
            text("""
                INSERT INTO care_plan_tasks (care_plan_id, assigned_to_user_id, description, trigger_text, status, created_at, updated_at)
                VALUES (:cp_id, :user_id, :desc, :trig, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "cp_id": body.care_plan_id,
                "user_id": body.assigned_to_user_id,
                "desc": body.description,
                "trig": body.trigger_text
            }
        )
        
        task_id = result_insert.lastrowid
        db.commit()

        return CreateFollowUpTaskResponse(
            task_id=task_id,
            care_plan_id=body.care_plan_id,
            assigned_to_user_id=body.assigned_to_user_id,
            description=body.description,
            trigger_text=body.trigger_text
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/contact_notes", response_model=LogContactNoteResponse, operation_id="log_contact_note")
def log_contact_note(body: LogContactNoteRequest):
    db = SessionLocal()
    try:
        # Verify care plan exists
        cp_check = db.execute(text("SELECT id FROM care_plans WHERE id = :cp_id"), {"cp_id": body.care_plan_id}).fetchone()
        if not cp_check:
            raise HTTPException(status_code=404, detail="Care plan not found")

        # Verify author user exists
        user_check = db.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": body.author_user_id}).fetchone()
        if not user_check:
            raise HTTPException(status_code=404, detail="Author user not found")

        # Insert Contact Note
        result_insert = db.execute(
            text("""
                INSERT INTO contact_notes (care_plan_id, author_user_id, note_text, transcript, created_at, updated_at)
                VALUES (:cp_id, :author_id, :note, :trans, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "cp_id": body.care_plan_id,
                "author_id": body.author_user_id,
                "note": body.note_text,
                "trans": body.transcript
            }
        )
        
        note_id = result_insert.lastrowid
        db.commit()

        return LogContactNoteResponse(
            note_id=note_id,
            care_plan_id=body.care_plan_id,
            author_user_id=body.author_user_id,
            note_text=body.note_text,
            transcript=body.transcript
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
