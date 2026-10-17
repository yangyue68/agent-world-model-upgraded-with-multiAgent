import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PatientDemographicsUpdate(BaseModel):
    email: str | None = None
    phone: str | None = None


class PatientResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    phone: str
    updated_at: str


@app.put("/patients/{patient_id}", response_model=PatientResponse, operation_id="update_patient_demographics")
def update_patient_demographics(patient_id: int, payload: PatientDemographicsUpdate):
    # Fetch current patient data to ensure existence and get immutable fields
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, first_name, last_name, email, phone, updated_at
                FROM patients
                WHERE id = :pid
            """),
            {"pid": patient_id}
        )
        row = result.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    current_email = row.email
    current_phone = row.phone

    new_email = payload.email if payload.email is not None else current_email
    new_phone = payload.phone if payload.phone is not None else current_phone

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Update the database
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE patients
                SET email = :email,
                    phone = :phone,
                    updated_at = :updated_at
                WHERE id = :pid
            """),
            {
                "email": new_email,
                "phone": new_phone,
                "updated_at": now_utc,
                "pid": patient_id
            }
        )

    return PatientResponse(
        id=row.id,
        first_name=row.first_name,
        last_name=row.last_name,
        email=new_email,
        phone=new_phone,
        updated_at=now_utc
    )
