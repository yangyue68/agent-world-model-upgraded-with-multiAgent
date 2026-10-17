import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class MergeRequest(BaseModel):
    target_patient_id: int
    notes: str | None = None


class PatientDetailsResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    date_of_birth: str | None = None
    gender: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    created_at: str
    updated_at: str
    merged_into_patient_id: int | None = None
    is_merged: int


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        # Handle ISO-8601 with Z suffix
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value.replace(" ", "T"))
    except Exception:
        return None


@app.post("/patients/{source_patient_id}/merge", operation_id="merge_patient_records")
def merge_patient_records(source_patient_id: int, body: MergeRequest):
    target_patient_id = body.target_patient_id
    notes = body.notes

    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Current user ID not found in environment.")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID format.")

    with engine.begin() as conn:
        # Fetch source patient
        src_row = conn.execute(
            text("SELECT * FROM patients WHERE id = :id"),
            {"id": source_patient_id}
        ).fetchone()
        
        if not src_row:
            raise HTTPException(status_code=404, detail=f"Source patient {source_patient_id} not found.")
            
        # Convert row to dict for easier access
        src_data = dict(src_row._mapping)

        # Check if already merged
        if src_data["is_merged"] == 1:
            raise HTTPException(status_code=400, detail=f"Patient {source_patient_id} has already been merged.")

        # Fetch target patient
        tgt_row = conn.execute(
            text("SELECT * FROM patients WHERE id = :id"),
            {"id": target_patient_id}
        ).fetchone()
        
        if not tgt_row:
            raise HTTPException(status_code=404, detail=f"Target patient {target_patient_id} not found.")
            
        tgt_data = dict(tgt_row._mapping)

        # Cannot merge into itself
        if source_patient_id == target_patient_id:
            raise HTTPException(status_code=400, detail="Cannot merge a patient record into itself.")

        # Determine which fields to keep based on "most recent information"
        # We compare updated_at timestamps. If equal or missing, we might prefer non-null values from source if target is null? 
        # The prompt says "keeping the most recent information". Usually implies comparing update times.
        
        src_updated = _parse_datetime(src_data["updated_at"])
        tgt_updated = _parse_datetime(tgt_data["updated_at"])
        
        # Default assumption: Target is kept unless Source is strictly newer? 
        # Or do we merge field-by-field? "Combine ... into a single profile, keeping the most recent information."
        # Standard ETL logic for duplicates often takes the latest version of each field or the whole record.
        # Given the schema allows updating specific columns, let's look at the example data.
        # Susan (1009) updated_at: 2024-03-15 16:30:00
        # Sue (1010) updated_at: 2024-03-20 11:10:00
        # Sue is more recently updated. So generally Sue's data should prevail where conflicts exist, 
        # but we need to transfer all related data to Sue (target).
        
        # However, sometimes "most recent" applies per field. Let's assume we want to preserve the target's identity 
        # but potentially enrich it with newer info from source if source was updated later for specific fields?
        # Actually, simpler interpretation: The target record remains the primary key holder. 
        # We update the target record's fields if the source record has newer updates for those specific fields?
        # Or just take the entire source record if it's newer? No, that would change the ID.
        
        # Let's stick to transferring relationships and marking source as merged.
        # For the patient details themselves: usually, you keep the target's core identity (ID) but may update contact info 
        # if the source had more recent changes. But since IDs are different, we can't easily swap them without breaking FKs 
        # unless we move everything. Moving everything means updating FKs in other tables.
        
        # Strategy:
        # 1. Update Target Patient record with any fields from Source that are considered "more recent" or simply fill gaps?
        #    The spec says "keeping the most recent information". 
        #    Let's compare `updated_at`. If Source.updated_at > Target.updated_at, maybe we copy all mutable fields?
        #    But wait, if we copy all fields, why keep Target ID? Because Target ID is referenced by others? 
        #    No, we are moving references TO Target.
        #    
        #    Let's look at the data again.
        #    1009 (Susan): Phone 555-0109, Email susan.miller@..., Address 22 Birch Lane... Updated 2024-03-15
        #    1010 (Sue):   Phone 555-0110, Email sue.miller@...,     Address 22 Birch Ln...      Updated 2024-03-20
        #    
        #    Since 1010 is newer, its data is likely the "current" truth. 
        #    However, what if 1009 had a correction made after 1010? Then 1009 would be newer.
        #    
        #    Implementation Plan:
        #    - Compare `updated_at` of both records.
        #    - If Source is newer than Target, we might want to overwrite Target's demographic/contact fields with Source's?
        #      BUT, the instruction says "Merge ... into a single profile". It doesn't explicitly say "overwrite target with source".
        #      It says "keeping the most recent information". This implies a field-level or record-level resolution.
        #      
        #      Common practice in such tasks:
        #      1. Move all child records (appointments, etc.) from Source to Target.
        #      2. Mark Source as merged (is_merged=1, merged_into_patient_id=Target).
        #      3. Optionally update Target's attributes if Source has "better/newer" data.
        #      
        #      Let's implement a conservative approach:
        #      - Transfer all foreign keys pointing to Source to point to Target.
        #      - Log the merge.
        #      - Update Source status.
        #      - Regarding "most recent information": If the task implies resolving conflict between the two patient rows themselves,
        #        we should probably ensure the Target row reflects the most up-to-date state. 
        #        If Source.updated_at > Target.updated_at, we could copy Source's name/dob/gender/phone/email/address to Target.
        #        Otherwise, we leave Target as is (since it's already newer).
        
        final_first_name = tgt_data["first_name"]
        final_last_name = tgt_data["last_name"]
        final_dob = tgt_data["date_of_birth"]
        final_gender = tgt_data["gender"]
        final_phone = tgt_data["phone"]
        final_email = tgt_data["email"]
        final_address = tgt_data["address"]
        
        # Logic: If Source is strictly newer, adopt Source's personal details for the Target record.
        if src_updated and tgt_updated:
            if src_updated > tgt_updated:
                final_first_name = src_data["first_name"]
                final_last_name = src_data["last_name"]
                final_dob = src_data["date_of_birth"]
                final_gender = src_data["gender"]
                final_phone = src_data["phone"]
                final_email = src_data["email"]
                final_address = src_data["address"]
        elif src_updated and not tgt_updated:
             # If target has no timestamp but source does, assume source is valid/current? 
             # Unlikely given defaults, but safe to handle.
             pass

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Update Appointments
        conn.execute(
            text("UPDATE appointments SET patient_id = :target_id, updated_at = :now WHERE patient_id = :source_id"),
            {"target_id": target_patient_id, "source_id": source_patient_id, "now": now_iso}
        )

        # 2. Update Medical Records
        conn.execute(
            text("UPDATE medical_records SET patient_id = :target_id, updated_at = :now WHERE patient_id = :source_id"),
            {"target_id": target_patient_id, "source_id": source_patient_id, "now": now_iso}
        )

        # 3. Update Prescriptions
        conn.execute(
            text("UPDATE prescriptions SET patient_id = :target_id, updated_at = :now WHERE patient_id = :source_id"),
            {"target_id": target_patient_id, "source_id": source_patient_id, "now": now_iso}
        )

        # 4. Update Billing
        conn.execute(
            text("UPDATE billing SET patient_id = :target_id, updated_at = :now WHERE patient_id = :source_id"),
            {"target_id": target_patient_id, "source_id": source_patient_id, "now": now_iso}
        )

        # 5. Insert Merge Log
        conn.execute(
            text("""
                INSERT INTO patient_merge_log (source_patient_id, target_patient_id, merged_by_user_id, merged_at, notes)
                VALUES (:src, :tgt, :user, :time, :notes)
            """),
            {
                "src": source_patient_id,
                "tgt": target_patient_id,
                "user": current_user_id,
                "time": now_iso,
                "notes": notes
            }
        )

        # 6. Update Source Patient Status
        conn.execute(
            text("""
                UPDATE patients 
                SET is_merged = 1, 
                    merged_into_patient_id = :target_id,
                    updated_at = :now
                WHERE id = :source_id
            """),
            {"target_id": target_patient_id, "source_id": source_patient_id, "now": now_iso}
        )

        # 7. Update Target Patient Details (if needed based on recency logic above)
        # Only update if we determined we should adopt source data OR always touch updated_at?
        # The spec says "keeping the most recent information". 
        # If we didn't adopt source data (because target was newer), we still performed a merge action.
        # Should we update target's updated_at? Probably yes, to reflect the structural change/merge event.
        
        conn.execute(
            text("""
                UPDATE patients 
                SET first_name = :fn,
                    last_name = :ln,
                    date_of_birth = :dob,
                    gender = :g,
                    phone = :p,
                    email = :e,
                    address = :a,
                    updated_at = :now
                WHERE id = :target_id
            """),
            {
                "fn": final_first_name,
                "ln": final_last_name,
                "dob": final_dob,
                "g": final_gender,
                "p": final_phone,
                "e": final_email,
                "a": final_address,
                "now": now_iso,
                "target_id": target_patient_id
            }
        )

    return {
        "message": f"Successfully merged patient {source_patient_id} into {target_patient_id}",
        "merged_patient_id": target_patient_id,
        "source_patient_id": source_patient_id,
        "target_patient_id": target_patient_id
    }


@app.get("/patients/{patient_id}", operation_id="get_patient_details")
def get_patient_details(patient_id: int):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM patients WHERE id = :id"),
            {"id": patient_id}
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Patient not found")

    data = dict(row._mapping)
    
    # Ensure types match Pydantic model expectations
    # SQLAlchemy returns datetime objects for DATETIME columns depending on driver/config.
    # In SQLite via SQLAlchemy default, they might come back as strings or datetimes.
    # The DDL uses DEFAULT CURRENT_TIMESTAMP which is string-like in some contexts but typed as DATETIME.
    # To be safe, convert to string if it's a datetime object, otherwise assume it's already string-compatible.
    
    def fmt_dt(val):
        if isinstance(val, datetime):
            return val.strftime("%Y-%m-%d %H:%M:%S")
        return val

    return PatientDetailsResponse(
        id=data["id"],
        first_name=data["first_name"],
        last_name=data["last_name"],
        date_of_birth=data["date_of_birth"],
        gender=data["gender"],
        phone=data["phone"],
        email=data["email"],
        address=data["address"],
        created_at=fmt_dt(data["created_at"]),
        updated_at=fmt_dt(data["updated_at"]),
        merged_into_patient_id=data["merged_into_patient_id"],
        is_merged=data["is_merged"]
    )
