import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PatientUpdateRequest(BaseModel):
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    preferred_contact_method: str | None = None


class PatientResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    address: str | None
    city: str | None
    state: str | None
    zip_code: str | None
    preferred_contact_method: str | None
    updated_at: datetime


@app.put("/patients/{patient_id}", response_model=PatientResponse, operation_id="update_patient_address_and_contact")
def update_patient_address_and_contact(patient_id: int, payload: PatientUpdateRequest):
    # Build dynamic SET clause based on provided fields
    updates = []
    params = {"pid": patient_id}

    if payload.address is not None:
        updates.append("address = :address")
        params["address"] = payload.address
    if payload.city is not None:
        updates.append("city = :city")
        params["city"] = payload.city
    if payload.state is not None:
        updates.append("state = :state")
        params["state"] = payload.state
    if payload.zip_code is not None:
        updates.append("zip_code = :zip_code")
        params["zip_code"] = payload.zip_code
    if payload.preferred_contact_method is not None:
        # Validate against enum values defined in schema CHECK constraint
        allowed_methods = ["phone", "email", "mail", "text"]
        if payload.preferred_contact_method not in allowed_methods:
            raise HTTPException(status_code=422, detail=f"Invalid preferred_contact_method. Must be one of {allowed_methods}")
        updates.append("preferred_contact_method = :preferred_contact_method")
        params["preferred_contact_method"] = payload.preferred_contact_method

    if not updates:
        # If no fields to update, just fetch and return current data? 
        # Or raise error? Spec says "can be updated together". Usually PUT implies full replacement or partial depending on design.
        # Given the optional nature of all body fields, we treat it as PATCH-like behavior within a PUT endpoint structure often seen in these tasks.
        # However, if nothing changes, we still need to return the object. Let's proceed to fetch.
        pass
    
    # Always update the timestamp if any field was provided, or even if none? 
    # Standard practice for "Update" endpoints is to touch updated_at if a change occurred.
    # But since we don't know if a change occurred without comparing old/new easily in SQL without extra queries,
    # and the spec implies updating specific fields, let's assume if the request is valid, we perform the update.
    # To be safe and consistent with typical ORM behaviors where saving touches timestamps:
    
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    updates.append("updated_at = :updated_at")
    params["updated_at"] = now_str

    set_clause = ", ".join(updates)
    
    sql_update = f"""
        UPDATE patients
        SET {set_clause}
        WHERE id = :pid
    """
    
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql_update), params)
            
            # Check if row exists (affected rows == 0 might mean ID not found)
            if result.rowcount == 0:
                # Verify if patient actually doesn't exist vs no changes made (though we always update updated_at so rowcount should be 1 if ID exists)
                check_exists = conn.execute(text("SELECT COUNT(*) FROM patients WHERE id = :pid"), {"pid": patient_id}).scalar()
                if check_exists == 0:
                    raise HTTPException(status_code=404, detail="Patient not found")
                
                # If count > 0 but rowcount 0, something weird happened, but logically shouldn't occur given we update updated_at.
                # Fallback to fetching anyway.
            
            # Fetch the updated record
            select_sql = """
                SELECT id, first_name, last_name, address, city, state, zip_code, preferred_contact_method, updated_at
                FROM patients
                WHERE id = :pid
            """
            row = conn.execute(text(select_sql), {"pid": patient_id}).fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="Patient not found after update")
            
            # Construct response model manually from Row tuple
            # Columns order matches SELECT statement above
            resp_data = {
                "id": row[0],
                "first_name": row[1],
                "last_name": row[2],
                "address": row[3],
                "city": row[4],
                "state": row[5],
                "zip_code": row[6],
                "preferred_contact_method": row[7],
                "updated_at": row[8]
            }
            
            return PatientResponse(**resp_data)
            
    except Exception as e:
        # Re-raise HTTPExceptions
        if isinstance(e, HTTPException):
            raise e
        # Log other errors if needed, otherwise generic 500
        raise HTTPException(status_code=500, detail=str(e))
