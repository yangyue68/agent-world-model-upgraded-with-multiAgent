import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CreatePatientRequest(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: str
    phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    insurance_policy_name: str
    member_id: str
    group_number: Optional[str] = None


class CreatePatientResponse(BaseModel):
    patient_id: int
    insurance_assignment_id: int
    policy_id: int


@app.post(
    "/patients",
    response_model=CreatePatientResponse,
    operation_id="create_patient_with_insurance",
    summary="Create a new patient and assign an insurance policy",
    description="Register a new patient with the provided personal details and assign them an insurance policy. The insurance policy must already exist in the system. Returns the created patient's ID and insurance assignment details.",
    tags=["PrimaryResource"],
)
def create_patient_with_insurance(request: CreatePatientRequest):
    # Validate that the insurance policy exists
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM insurance_policies WHERE policy_name = :policy_name"),
            {"policy_name": request.insurance_policy_name},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Insurance policy '{request.insurance_policy_name}' not found",
            )
        policy_id = row[0]

        # Insert the new patient
        insert_patient_sql = text(
            """
            INSERT INTO patients (first_name, last_name, date_of_birth, phone, address_line1, address_line2, city, state, postal_code)
            VALUES (:first_name, :last_name, :date_of_birth, :phone, :address_line1, :address_line2, :city, :state, :postal_code)
            """
        )
        conn.execute(
            insert_patient_sql,
            {
                "first_name": request.first_name,
                "last_name": request.last_name,
                "date_of_birth": request.date_of_birth,
                "phone": request.phone,
                "address_line1": request.address_line1,
                "address_line2": request.address_line2,
                "city": request.city,
                "state": request.state,
                "postal_code": request.postal_code,
            },
        )
        patient_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

        # Insert the patient insurance assignment
        insert_pi_sql = text(
            """
            INSERT INTO patient_insurance (patient_id, policy_id, member_id, group_number)
            VALUES (:patient_id, :policy_id, :member_id, :group_number)
            """
        )
        conn.execute(
            insert_pi_sql,
            {
                "patient_id": patient_id,
                "policy_id": policy_id,
                "member_id": request.member_id,
                "group_number": request.group_number,
            },
        )
        insurance_assignment_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

        conn.commit()

    return CreatePatientResponse(
        patient_id=patient_id,
        insurance_assignment_id=insurance_assignment_id,
        policy_id=policy_id,
    )
