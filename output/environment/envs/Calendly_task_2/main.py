import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class AvailabilityRule(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6, description="Day of week (0=Sunday, 1=Monday, ..., 6=Saturday)")
    start_time: str = Field(..., pattern=r"^[0-2][0-9]:[0-5][0-9]$", description="Start time in HH:MM format")
    end_time: str = Field(..., pattern=r"^[0-2][0-9]:[0-5][0-9]$", description="End time in HH:MM format")


class Question(BaseModel):
    question_text: str = Field(..., description="Text of the question")
    question_type: str = Field(..., description="Type of the question")
    is_required: bool = Field(..., description="Whether the question is required")


class CreateEventTypeRequest(BaseModel):
    name: str = Field(..., description="Name of the event type")
    duration_minutes: int = Field(..., gt=0, description="Duration of the event in minutes")
    location: str = Field(..., description="Location of the event")
    calendar_sync_required: bool = Field(..., description="Whether calendar sync is required")
    availability_rules: List[AvailabilityRule] = Field(..., min_items=1, description="Array of availability rules for the event type")
    questions: List[Question] = Field(..., min_items=1, description="Array of questions for the event type")


class CreateEventTypeResponse(BaseModel):
    event_type_id: int
    availability_rule_ids: List[int]
    question_ids: List[int]


@app.post("/event_types", response_model=CreateEventTypeResponse, operation_id="create_event_type")
def create_event_type(payload: CreateEventTypeRequest):
    # Get current user ID from environment
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    db = SessionLocal()
    try:
        # Verify user exists
        result = db.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Insert event type
        cal_sync_int = 1 if payload.calendar_sync_required else 0
        
        insert_event_stmt = text("""
            INSERT INTO event_types (user_id, name, duration_minutes, location, calendar_sync_required)
            VALUES (:user_id, :name, :duration_minutes, :location, :calendar_sync_required)
        """)
        
        res = db.execute(insert_event_stmt, {
            "user_id": user_id,
            "name": payload.name,
            "duration_minutes": payload.duration_minutes,
            "location": payload.location,
            "calendar_sync_required": cal_sync_int
        })
        event_type_id = res.lastrowid

        # Insert availability rules
        availability_rule_ids = []
        insert_avail_stmt = text("""
            INSERT INTO availability_rules (event_type_id, day_of_week, start_time, end_time, timezone)
            VALUES (:event_type_id, :day_of_week, :start_time, :end_time, 'America/Los_Angeles')
        """)
        
        for rule in payload.availability_rules:
            res = db.execute(insert_avail_stmt, {
                "event_type_id": event_type_id,
                "day_of_week": rule.day_of_week,
                "start_time": rule.start_time,
                "end_time": rule.end_time
            })
            availability_rule_ids.append(res.lastrowid)

        # Insert questions
        question_ids = []
        insert_question_stmt = text("""
            INSERT INTO event_questions (event_type_id, question_text, question_type, is_required, sort_order)
            VALUES (:event_type_id, :question_text, :question_type, :is_required, :sort_order)
        """)
        
        for idx, q in enumerate(payload.questions):
            is_req_int = 1 if q.is_required else 0
            res = db.execute(insert_question_stmt, {
                "event_type_id": event_type_id,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "is_required": is_req_int,
                "sort_order": idx + 1
            })
            question_ids.append(res.lastrowid)

        db.commit()

        return CreateEventTypeResponse(
            event_type_id=event_type_id,
            availability_rule_ids=availability_rule_ids,
            question_ids=question_ids
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
