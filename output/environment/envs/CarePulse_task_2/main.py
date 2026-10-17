import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from typing import Optional

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UpdateInsuranceRequest(BaseModel):
    insurance_provider_name: str = Field(..., description="Name of the insurance provider (e.g., 'Blue Cross Blue Shield')")
    member_id: str = Field(..., description="Member ID for the insurance policy")
    group_number: Optional[str] = Field(None, description="Group number associated with the insurance policy")
    policyholder_name: str = Field(..., description="Full name of the policyholder")


class UpdatedInsuranceResponse(BaseModel):
    id: int
    user_id: int
    insurance_provider_id: int
    member_id: str
    group_number: Optional[str]
    policyholder_name: str
    is_primary: bool


class UpdateInsuranceResponse(BaseModel):
    message: str
    updated_insurance: UpdatedInsuranceResponse


@app.put("/users/{user_id}/primary_insurance", response_model=UpdateInsuranceResponse, operation_id="update_user_primary_insurance")
def update_user_primary_insurance(
    user_id: int = Path(..., description="Internal ID of the user whose primary insurance is being updated"),
    body: UpdateInsuranceRequest = None,
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: No current user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid current user context")

    # Verify the user exists and matches the path parameter if scope requires it. 
    # The spec says user_scope: "current_user". Usually this means the logged-in user can only update their own info.
    # However, the endpoint takes user_id in path. Let's assume strict check: current_user must equal path user_id.
    if current_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You can only update your own insurance information")

    with engine.connect() as conn:
        # 1. Find the insurance provider ID by name
        res = conn.execute(text("SELECT id FROM insurance_providers WHERE name = :name"), {"name": body.insurance_provider_name})
        provider_row = res.fetchone()
        
        if not provider_row:
            raise HTTPException(status_code=404, detail=f"Insurance provider '{body.insurance_provider_name}' not found")
        
        provider_id = provider_row[0]

        # 2. Check if there is already an existing insurance record for this user + provider + member_id
        # The unique constraint is on (user_id, insurance_provider_id, member_id)
        res_existing = conn.execute(
            text("""
                SELECT id, is_primary 
                FROM patient_insurances 
                WHERE user_id = :uid AND insurance_provider_id = :pid AND member_id = :mid
            """),
            {"uid": user_id, "pid": provider_id, "mid": body.member_id}
        )
        existing_record = res_existing.fetchone()

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if existing_record:
            ins_id = existing_record[0]
            
            # If it's already primary, we might just need to ensure other fields are updated?
            # But the task implies setting THIS one as primary.
            # First, unset any OTHER primary insurance for this user
            conn.execute(
                text("""
                    UPDATE patient_insurances 
                    SET is_primary = 0, updated_at = :now
                    WHERE user_id = :uid AND is_primary = 1 AND id != :ins_id
                """),
                {"uid": user_id, "ins_id": ins_id, "now": now}
            )
            
            # Then update the target record to be primary and update details
            conn.execute(
                text("""
                    UPDATE patient_insurances 
                    SET group_number = :gn, 
                        policyholder_name = :pn, 
                        is_primary = 1, 
                        updated_at = :now
                    WHERE id = :ins_id
                """),
                {
                    "gn": body.group_number,
                    "pn": body.policyholder_name,
                    "ins_id": ins_id,
                    "now": now
                }
            )
        else:
            # Unset any existing primary insurance for this user
            conn.execute(
                text("""
                    UPDATE patient_insurances 
                    SET is_primary = 0, updated_at = :now
                    WHERE user_id = :uid AND is_primary = 1
                """),
                {"uid": user_id, "now": now}
            )
            
            # Insert new insurance record
            result_insert = conn.execute(
                text("""
                    INSERT INTO patient_insurances 
                    (user_id, insurance_provider_id, member_id, group_number, policyholder_name, is_primary, created_at, updated_at)
                    VALUES (:uid, :pid, :mid, :gn, :pn, 1, :now, :now)
                """),
                {
                    "uid": user_id,
                    "pid": provider_id,
                    "mid": body.member_id,
                    "gn": body.group_number,
                    "pn": body.policyholder_name,
                    "now": now
                }
            )
            ins_id = result_insert.lastrowid

        conn.commit()

        # Fetch the final state to return
        res_final = conn.execute(
            text("""
                SELECT id, user_id, insurance_provider_id, member_id, group_number, policyholder_name, is_primary
                FROM patient_insurances
                WHERE id = :ins_id
            """),
            {"ins_id": ins_id}
        )
        final_row = res_final.fetchone()
        
        if not final_row:
             raise HTTPException(status_code=500, detail="Failed to retrieve updated insurance record")

        updated_insurance = UpdatedInsuranceResponse(
            id=final_row[0],
            user_id=final_row[1],
            insurance_provider_id=final_row[2],
            member_id=final_row[3],
            group_number=final_row[4],
            policyholder_name=final_row[5],
            is_primary=bool(final_row[6])
        )

        return UpdateInsuranceResponse(
            message="Primary insurance updated successfully",
            updated_insurance=updated_insurance
        )
