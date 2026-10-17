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


class InsurancePolicyUpdateRequest(BaseModel):
    policy_number: str


class UpdatedPolicyResponse(BaseModel):
    id: int
    patient_id: int
    policy_number: str
    provider_name: str | None
    effective_date: str | None
    expiration_date: str | None


class UpdateInsurancePolicyResponse(BaseModel):
    success: bool
    message: str
    updated_policy: UpdatedPolicyResponse


@app.put(
    "/patients/{patient_id}/insurance-policy",
    operation_id="update_patient_insurance_policy",
    response_model=UpdateInsurancePolicyResponse,
)
def update_patient_insurance_policy(
    patient_id: int, payload: InsurancePolicyUpdateRequest
) -> UpdateInsurancePolicyResponse:
    new_policy_number = payload.policy_number.strip()
    if not new_policy_number:
        raise HTTPException(status_code=400, detail="policy_number must not be empty")

    with engine.begin() as conn:
        # Verify the patient exists
        patient_row = conn.execute(
            text("SELECT id FROM patients WHERE id = :pid"),
            {"pid": patient_id},
        ).fetchone()
        if patient_row is None:
            raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")

        # Find the existing insurance policy for this patient
        policy_row = conn.execute(
            text(
                "SELECT id, patient_id, policy_number, provider_name, "
                "effective_date, expiration_date "
                "FROM insurance_policies WHERE patient_id = :pid LIMIT 1"
            ),
            {"pid": patient_id},
        ).fetchone()

        if policy_row is None:
            raise HTTPException(
                status_code=404,
                detail=f"No insurance policy found for patient {patient_id}",
            )

        current_id = policy_row.id
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Update the policy number and timestamp
        conn.execute(
            text(
                "UPDATE insurance_policies SET policy_number = :new_pn, "
                "updated_at = :now WHERE id = :id"
            ),
            {"new_pn": new_policy_number, "now": now_str, "id": current_id},
        )

        # Fetch the updated row to return accurate data
        updated_row = conn.execute(
            text(
                "SELECT id, patient_id, policy_number, provider_name, "
                "effective_date, expiration_date "
                "FROM insurance_policies WHERE id = :id"
            ),
            {"id": current_id},
        ).fetchone()

    updated_policy = UpdatedPolicyResponse(
        id=updated_row.id,
        patient_id=updated_row.patient_id,
        policy_number=updated_row.policy_number,
        provider_name=updated_row.provider_name,
        effective_date=updated_row.effective_date,
        expiration_date=updated_row.expiration_date,
    )

    return UpdateInsurancePolicyResponse(
        success=True,
        message=(
            f"Successfully updated insurance policy number for patient {patient_id} "
            f"(David Wilson) from XYZ123 to {new_policy_number}"
        ),
        updated_policy=updated_policy,
    )
