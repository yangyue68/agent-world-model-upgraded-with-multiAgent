import os
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class DependentCreate(BaseModel):
    name: str
    date_of_birth: str
    gender: str
    relationship: str


@app.post("/primary_resource/dependents", operation_id="add_dependent")
def add_dependent(payload: DependentCreate):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Validate gender against allowed values from schema CHECK constraint
    if payload.gender not in ("male", "female", "other"):
        raise HTTPException(status_code=422, detail="Invalid gender value")

    # Parse and validate date of birth format YYYY-MM-DD
    try:
        dob_date = datetime.strptime(payload.date_of_birth, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date_of_birth format. Expected YYYY-MM-DD.")

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        # Verify user exists to avoid FK violation later (optional but good practice for clear errors)
        user_exists = conn.execute(
            text("SELECT 1 FROM users WHERE id = :uid"),
            {"uid": user_id}
        ).fetchone()
        
        if not user_exists:
            raise HTTPException(status_code=404, detail="User not found")

        result = conn.execute(
            text("""
                INSERT INTO dependents (user_id, name, date_of_birth, gender, relationship, is_active, created_at, updated_at)
                VALUES (:user_id, :name, :dob, :gender, :relationship, 1, :now, :now)
            """),
            {
                "user_id": user_id,
                "name": payload.name,
                "dob": dob_date.isoformat(),
                "gender": payload.gender,
                "relationship": payload.relationship,
                "now": now
            }
        )
        
        new_id = result.lastrowid
        
        row = conn.execute(
            text("""
                SELECT id, user_id, name, date_of_birth, gender, relationship, is_active, created_at, updated_at
                FROM dependents
                WHERE id = :id
            """),
            {"id": new_id}
        ).fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created dependent")

    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "date_of_birth": row.date_of_birth,
        "gender": row.gender,
        "relationship": row.relationship,
        "is_active": row.is_active,
        "created_at": row.created_at,
        "updated_at": row.updated_at
    }
