import os
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class LeadCreate(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    source: str | None = None
    assigned_to: int


class FollowUpCreate(BaseModel):
    lead_id: int
    assigned_to: int
    scheduled_at: str
    subject: str
    notes: str | None = None


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")


@app.post("/leads", operation_id="create_lead")
def create_lead(lead_data: LeadCreate):
    current_user_id = get_current_user_id()
    
    # Verify assigned_to exists and is a valid user
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id FROM users WHERE id = :id"), {"id": lead_data.assigned_to})
        if not result.fetchone():
            raise HTTPException(status_code=400, detail="Assigned salesperson not found")
        
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        
        insert_result = conn.execute(
            text("""
                INSERT INTO leads (full_name, email, phone, source, status, assigned_to, created_by, created_at, updated_at)
                VALUES (:full_name, :email, :phone, :source, 'new', :assigned_to, :created_by, :now, :now)
            """),
            {
                "full_name": lead_data.full_name,
                "email": lead_data.email,
                "phone": lead_data.phone,
                "source": lead_data.source,
                "assigned_to": lead_data.assigned_to,
                "created_by": current_user_id,
                "now": now,
            }
        )
        conn.commit()
        
        lead_id = insert_result.lastrowid
        
        result = conn.execute(
            text("""
                SELECT id, full_name, email, phone, source, status, assigned_to, created_by, created_at, updated_at
                FROM leads WHERE id = :id
            """),
            {"id": lead_id}
        )
        row = result.fetchone()
        
        return {
            "id": row[0],
            "full_name": row[1],
            "email": row[2],
            "phone": row[3],
            "source": row[4],
            "status": row[5],
            "assigned_to": row[6],
            "created_by": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }


@app.post("/follow_ups", operation_id="create_follow_up")
def create_follow_up(follow_up_data: FollowUpCreate):
    current_user_id = get_current_user_id()
    
    # Parse the scheduled_at timestamp to handle both T...Z and space-separated formats
    try:
        # Try ISO format with T separator first
        dt = datetime.fromisoformat(follow_up_data.scheduled_at.replace('Z', '+00:00'))
    except ValueError:
        try:
            # Try space-separated format
            dt = datetime.strptime(follow_up_data.scheduled_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scheduled_at format")
    
    # Normalize to string for storage
    scheduled_at_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    
    with engine.connect() as conn:
        # Verify lead exists
        result = conn.execute(text("SELECT id FROM leads WHERE id = :id"), {"id": follow_up_data.lead_id})
        if not result.fetchone():
            raise HTTPException(status_code=400, detail="Lead not found")
        
        # Verify assigned_to exists
        result = conn.execute(text("SELECT id FROM users WHERE id = :id"), {"id": follow_up_data.assigned_to})
        if not result.fetchone():
            raise HTTPException(status_code=400, detail="Assigned user not found")
        
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        
        insert_result = conn.execute(
            text("""
                INSERT INTO follow_ups (lead_id, assigned_to, scheduled_at, subject, notes, status, created_by, created_at, updated_at)
                VALUES (:lead_id, :assigned_to, :scheduled_at, :subject, :notes, 'pending', :created_by, :now, :now)
            """),
            {
                "lead_id": follow_up_data.lead_id,
                "assigned_to": follow_up_data.assigned_to,
                "scheduled_at": scheduled_at_str,
                "subject": follow_up_data.subject,
                "notes": follow_up_data.notes,
                "created_by": current_user_id,
                "now": now,
            }
        )
        conn.commit()
        
        follow_up_id = insert_result.lastrowid
        
        result = conn.execute(
            text("""
                SELECT id, lead_id, assigned_to, scheduled_at, subject, notes, status, created_by, created_at, updated_at
                FROM follow_ups WHERE id = :id
            """),
            {"id": follow_up_id}
        )
        row = result.fetchone()
        
        return {
            "id": row[0],
            "lead_id": row[1],
            "assigned_to": row[2],
            "scheduled_at": row[3],
            "subject": row[4],
            "notes": row[5],
            "status": row[6],
            "created_by": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }
