import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class DeleteResponse(BaseModel):
    message: str
    deleted_policy_id: int | None = None


@app.delete(
    "/patients/{patient_id}/insurance-policies/secondary",
    response_model=DeleteResponse,
    operation_id="remove_secondary_insurance_policy",
)
def remove_secondary_insurance_policy(patient_id: int):
    with engine.connect() as conn:
        # Check if patient exists
        result = conn.execute(
            text("SELECT id FROM patients WHERE id = :pid"),
            {"pid": patient_id}
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Patient not found")

        # Find the secondary insurance policy for this patient
        result = conn.execute(
            text("""
                SELECT id FROM insurance_policies 
                WHERE patient_id = :pid AND policy_type = 'secondary'
            """),
            {"pid": patient_id}
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No secondary insurance policy found for this patient")

        policy_id = row[0]

        # Delete the secondary insurance policy
        conn.execute(
            text("DELETE FROM insurance_policies WHERE id = :policy_id"),
            {"policy_id": policy_id}
        )
        conn.commit()

        return DeleteResponse(
            message=f"Secondary insurance policy {policy_id} removed successfully",
            deleted_policy_id=policy_id
        )
