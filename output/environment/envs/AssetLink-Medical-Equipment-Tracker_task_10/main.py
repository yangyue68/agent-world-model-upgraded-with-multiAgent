import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class UserSearchResponse(BaseModel):
    id: int
    full_name: str


class QuarantineRequest(BaseModel):
    reason: str
    quarantine_start_time: str


class QuarantineResponse(BaseModel):
    equipment_id: int
    quarantine_record_id: int
    notification_id: int


class EquipmentResponse(BaseModel):
    id: int
    asset_tag: str
    name: str
    type: Optional[str]
    status: str
    location: Optional[str]


class UserRoleResponse(BaseModel):
    id: int
    username: str
    full_name: Optional[str]
    email: Optional[str]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/users/search", response_model=List[UserSearchResponse], operation_id="search_users")
def search_users(full_name: str = Query(...)):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, full_name FROM users WHERE full_name LIKE :pattern"),
            {"pattern": f"%{full_name}%"}
        )
        rows = result.fetchall()
        return [{"id": row[0], "full_name": row[1]} for row in rows]


@app.put("/equipment/{asset_tag}/quarantine", response_model=QuarantineResponse, operation_id="quarantine_equipment")
def quarantine_equipment(asset_tag: str, body: QuarantineRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    # Parse the ISO 8601 timestamp
    try:
        # Handle both 'T...Z' and space-separated forms
        ts = body.quarantine_start_time.replace('Z', '+00:00')
        if 'T' in ts:
            dt = datetime.fromisoformat(ts)
        else:
            # Try parsing as space-separated
            dt = datetime.strptime(body.quarantine_start_time, "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
        
        # Store as naive UTC or with timezone? SQLite DATETIME typically stores as string.
        # The existing data uses format like "2024-05-12 13:22:00" (no T, no Z).
        # Let's normalize to that format for consistency with existing DB entries.
        quarantine_start_dt = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {str(e)}")

    with engine.begin() as conn:
        # 1. Find equipment by asset_tag
        eq_result = conn.execute(
            text("SELECT id, status FROM equipment WHERE asset_tag = :tag"),
            {"tag": asset_tag}
        ).fetchone()
        
        if not eq_result:
            raise HTTPException(status_code=404, detail="Equipment not found")
        
        equipment_id = eq_result[0]
        previous_status = eq_result[1]

        # 2. Update equipment status to 'Quarantine'
        conn.execute(
            text("UPDATE equipment SET status = :status, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"status": "Quarantine", "id": equipment_id}
        )

        # 3. Insert into equipment_status_history
        conn.execute(
            text("""
                INSERT INTO equipment_status_history 
                (equipment_id, previous_status, new_status, changed_by, notes)
                VALUES (:eq_id, :prev, :new, :user, :note)
            """),
            {
                "eq_id": equipment_id,
                "prev": previous_status,
                "new": "Quarantine",
                "user": current_user_id,
                "note": f"Quarantined due to: {body.reason}"
            }
        )

        # 4. Insert into quarantine_records
        qr_result = conn.execute(
            text("""
                INSERT INTO quarantine_records 
                (equipment_id, reason, quarantine_start_time, status, created_by)
                VALUES (:eq_id, :reason, :start_time, 'Active', :user)
            """),
            {
                "eq_id": equipment_id,
                "reason": body.reason,
                "start_time": quarantine_start_dt,
                "user": current_user_id
            }
        )
        quarantine_record_id = qr_result.lastrowid

        # 5. Find biomedical engineer 'J. Lopez'
        # Search for user with full_name containing 'J. Lopez' and role 'Biomedical Engineer'
        user_result = conn.execute(
            text("""
                SELECT id FROM users 
                WHERE full_name LIKE :name AND role = :role
            """),
            {"name": "%J. Lopez%", "role": "Biomedical Engineer"}
        ).fetchone()

        if not user_result:
            # Fallback: just search by name if role filter fails or vice versa, but spec says notify biomedical engineer J. Lopez
            # If not found, we might still need to send notification? But recipient_id is NOT NULL FK.
            # Let's assume it exists based on initial snapshot.
            raise HTTPException(status_code=404, detail="Biomedical engineer J. Lopez not found")
        
        recipient_id = user_result[0]

        # 6. Insert notification
        subject = "Equipment Placed in Quarantine"
        message = f"Asset {asset_tag} has been quarantined due to: {body.reason}. Please review."
        
        notif_result = conn.execute(
            text("""
                INSERT INTO notifications 
                (recipient_id, sender_id, subject, message, notification_type, related_equipment_id, related_quarantine_id, status, sent_at)
                VALUES (:rec, :sender, :subj, :msg, 'Internal Escalation', :eq_id, :qr_id, 'Sent', CURRENT_TIMESTAMP)
            """),
            {
                "rec": recipient_id,
                "sender": current_user_id,
                "subj": subject,
                "msg": message,
                "eq_id": equipment_id,
                "qr_id": quarantine_record_id
            }
        )
        notification_id = notif_result.lastrowid

        return {
            "equipment_id": equipment_id,
            "quarantine_record_id": quarantine_record_id,
            "notification_id": notification_id
        }


@app.get("/equipment/{asset_tag}", response_model=EquipmentResponse, operation_id="get_equipment_by_asset_tag")
def get_equipment_by_asset_tag(asset_tag: str):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, asset_tag, name, type, status, location FROM equipment WHERE asset_tag = :tag"),
            {"tag": asset_tag}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Equipment not found")
        
        return {
            "id": result[0],
            "asset_tag": result[1],
            "name": result[2],
            "type": result[3],
            "status": result[4],
            "location": result[5]
        }


@app.get("/users/role/{role}", response_model=List[UserRoleResponse], operation_id="get_users_by_role")
def get_users_by_role(role: str):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, username, full_name, email FROM users WHERE role = :role"),
            {"role": role}
        )
        rows = result.fetchall()
        return [
            {
                "id": row[0],
                "username": row[1],
                "full_name": row[2],
                "email": row[3]
            }
            for row in rows
        ]
