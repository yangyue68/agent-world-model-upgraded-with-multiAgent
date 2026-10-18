import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ProfileUpdateRequest(BaseModel):
    email: str
    phone: str


class ProfileResponse(BaseModel):
    id: int
    email: str
    phone: str
    updated_at: str


@app.put("/profile", response_model=ProfileResponse, operation_id="update_profile")
def update_profile(payload: ProfileUpdateRequest):
    # Get current user ID from environment variable injected by runtime
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    new_email = payload.email
    new_phone = payload.phone

    with engine.begin() as conn:
        # Check if the new email already exists for a different user
        stmt_check_email = text(
            "SELECT id FROM users WHERE email = :email AND id != :user_id"
        )
        result_check = conn.execute(stmt_check_email, {"email": new_email, "user_id": current_user_id})
        
        if result_check.fetchone():
            raise HTTPException(status_code=409, detail="Email already in use by another user")

        # Update the user's profile
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        stmt_update = text(
            """
            UPDATE users 
            SET email = :email, 
                phone = :phone, 
                updated_at = :updated_at 
            WHERE id = :user_id
            """
        )
        
        conn.execute(
            stmt_update,
            {
                "email": new_email,
                "phone": new_phone,
                "updated_at": now_utc,
                "user_id": current_user_id,
            },
        )

        # Fetch the updated record to return correct data including server-side defaults if any
        stmt_fetch = text(
            """
            SELECT id, email, phone, updated_at 
            FROM users 
            WHERE id = :user_id
            """
        )
        
        row = conn.execute(stmt_fetch, {"user_id": current_user_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        return ProfileResponse(
            id=row.id,
            email=row.email,
            phone=row.phone,
            updated_at=str(row.updated_at),
        )
