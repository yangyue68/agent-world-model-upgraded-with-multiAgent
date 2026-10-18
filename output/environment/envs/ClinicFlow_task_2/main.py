import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UpdatePatientPhoneRequest(BaseModel):
    phone: str


class AddSecondaryInsuranceRequest(BaseModel):
    insurer_name: str
    member_id: str
    group_number: str
    policy_type: str | None = None


@app.put("/patients/{patient_id}", operation_id="update_patient_phone")
def update_patient_phone(patient_id: int, body: UpdatePatientPhoneRequest):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, first_name, last_name, phone FROM patients WHERE id = :pid"),
            {"pid": patient_id},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Patient not found")

        conn.execute(
            text(
                "UPDATE patients SET phone = :phone, updated_at = CURRENT_TIMESTAMP WHERE id = :pid"
            ),
            {"phone": body.phone, "pid": patient_id},
        )
        conn.commit()

        return {
            "id": row[0],
            "first_name": row[1],
            "last_name": row[2],
            "phone": body.phone,
        }


@app.post("/patients/{patient_id}/insurance_policies", operation_id="add_secondary_insurance")
def add_secondary_insurance(patient_id: int, body: AddSecondaryInsuranceRequest):
    with engine.connect() as conn:
        # Verify patient exists
        result = conn.execute(
            text("SELECT id FROM patients WHERE id = :pid"),
            {"pid": patient_id},
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Patient not found")

        # Insert new secondary insurance policy (is_primary = 0)
        insert_result = conn.execute(
            text(
                """INSERT INTO insurance_policies 
                   (patient_id, insurer_name, member_id, group_number, policy_type, is_primary, created_at, updated_at)
                   VALUES (:patient_id, :insurer_name, :member_id, :group_number, :policy_type, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""
            ),
            {
                "patient_id": patient_id,
                "insurer_name": body.insurer_name,
                "member_id": body.member_id,
                "group_number": body.group_number,
                "policy_type": body.policy_type,
            },
        )
        conn.commit()

        new_id = insert_result.lastrowid

        # Fetch the inserted record to return full details
        fetch_result = conn.execute(
            text(
                "SELECT id, patient_id, insurer_name, member_id, group_number, policy_type, is_primary FROM insurance_policies WHERE id = :id"
            ),
            {"id": new_id},
        )
        row = fetch_result.fetchone()

        return {
            "id": row[0],
            "patient_id": row[1],
            "insurer_name": row[2],
            "member_id": row[3],
            "group_number": row[4],
            "policy_type": row[5],
            "is_primary": row[6],
        }
