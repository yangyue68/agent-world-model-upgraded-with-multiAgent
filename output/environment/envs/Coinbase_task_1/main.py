import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

app = FastAPI()


class IdentityVerificationRequest(BaseModel):
    legal_name: str = Field(..., description="Legal name of the user")
    date_of_birth: str = Field(..., description="Date of birth in YYYY-MM-DD format")
    residential_address: str = Field(..., description="Residential address of the user")
    ssn_last_four: str = Field(..., description="Last four digits of the user's Social Security Number")


class IdentityVerificationResponse(BaseModel):
    id: int
    user_id: int
    legal_name: str
    date_of_birth: str
    residential_address: str
    ssn_last_four: str
    status: str
    created_at: str
    updated_at: str


@app.post("/identity-verifications", response_model=IdentityVerificationResponse, operation_id="submit_identity_verification")
def submit_identity_verification(payload: IdentityVerificationRequest):
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Validate SSN last four digits
    if len(payload.ssn_last_four) != 4 or not payload.ssn_last_four.isdigit():
        raise HTTPException(status_code=422, detail="SSN last four must be exactly 4 digits")

    with SessionLocal() as db:
        # Check if user exists
        user_check = db.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": current_user_id}
        ).fetchone()
        
        if not user_check:
            raise HTTPException(status_code=404, detail="User not found")

        # Check if identity verification already exists for this user
        existing_iv = db.execute(
            text("SELECT * FROM identity_verifications WHERE user_id = :uid"),
            {"uid": current_user_id}
        ).fetchone()

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if existing_iv:
            # Update existing record
            db.execute(
                text("""
                    UPDATE identity_verifications 
                    SET legal_name = :legal_name,
                        date_of_birth = :dob,
                        residential_address = :address,
                        ssn_last_four = :ssn,
                        status = 'pending',
                        updated_at = :now
                    WHERE user_id = :uid
                """),
                {
                    "legal_name": payload.legal_name,
                    "dob": payload.date_of_birth,
                    "address": payload.residential_address,
                    "ssn": payload.ssn_last_four,
                    "uid": current_user_id,
                    "now": now
                }
            )
            db.commit()
            
            # Fetch updated record
            row = db.execute(
                text("SELECT * FROM identity_verifications WHERE user_id = :uid"),
                {"uid": current_user_id}
            ).fetchone()
        else:
            # Insert new record
            db.execute(
                text("""
                    INSERT INTO identity_verifications (
                        user_id, legal_name, date_of_birth, residential_address, 
                        ssn_last_four, status, created_at, updated_at
                    ) VALUES (
                        :uid, :legal_name, :dob, :address, 
                        :ssn, 'pending', :now, :now
                    )
                """),
                {
                    "uid": current_user_id,
                    "legal_name": payload.legal_name,
                    "dob": payload.date_of_birth,
                    "address": payload.residential_address,
                    "ssn": payload.ssn_last_four,
                    "now": now
                }
            )
            db.commit()
            
            # Fetch inserted record
            row = db.execute(
                text("SELECT * FROM identity_verifications WHERE user_id = :uid"),
                {"uid": current_user_id}
            ).fetchone()

        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve identity verification record")

        return IdentityVerificationResponse(
            id=row.id,
            user_id=row.user_id,
            legal_name=row.legal_name,
            date_of_birth=row.date_of_birth,
            residential_address=row.residential_address,
            ssn_last_four=row.ssn_last_four,
            status=row.status,
            created_at=str(row.created_at),
            updated_at=str(row.updated_at)
        )
