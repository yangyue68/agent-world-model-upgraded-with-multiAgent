import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ReservationResponse(BaseModel):
    id: int
    confirmation_number: str
    guest_id: int
    site_id: int
    check_in_date: str
    check_out_date: str
    status: str
    notes: str | None = None
    created_by: int
    created_at: str
    updated_at: str


class UpdateSiteRequest(BaseModel):
    new_site_id: int


class UpdateSiteResponse(BaseModel):
    message: str
    reservation_id: int
    old_site_id: int
    new_site_id: int


@app.get("/reservations/{confirmation_number}", response_model=ReservationResponse, operation_id="get_reservation_by_confirmation")
def get_reservation_by_confirmation(
    confirmation_number: str = Path(..., description="Unique identifier for the reservation")
):
    query = """
        SELECT 
            r.id,
            r.confirmation_number,
            r.guest_id,
            r.site_id,
            r.check_in_date,
            r.check_out_date,
            r.status,
            r.notes,
            r.created_by,
            r.created_at,
            r.updated_at
        FROM reservations r
        WHERE r.confirmation_number = :cn
    """
    with engine.connect() as conn:
        result = conn.execute(text(query), {"cn": confirmation_number}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Reservation not found")
    
    row = dict(result._mapping)
    # Ensure dates are strings in ISO format if they come back as date objects (though SQLite usually returns strings)
    # The schema says DATE/DATETIME, SQLAlchemy might return string or object depending on driver/version.
    # Given the prompt implies simple usage, we assume direct mapping works or cast to str if needed.
    # However, Pydantic will handle serialization. Let's ensure keys match model.
    
    return ReservationResponse(**row)


@app.put("/reservations/{reservation_id}/site", response_model=UpdateSiteResponse, operation_id="update_reservation_site")
def update_reservation_site(
    reservation_id: int = Path(..., description="Internal ID of the reservation to modify"),
    payload: UpdateSiteRequest = Body(...)
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        changed_by = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    new_site_id = payload.new_site_id

    with engine.begin() as conn:
        # 1. Get current reservation details
        res_query = "SELECT id, site_id, check_in_date, check_out_date, status FROM reservations WHERE id = :rid"
        res_row = conn.execute(text(res_query), {"rid": reservation_id}).fetchone()
        
        if not res_row:
            raise HTTPException(status_code=404, detail="Reservation not found")
        
        old_site_id = res_row[1]
        
        # Verify new site exists and is active? Spec doesn't explicitly require validation but good practice.
        # However, strict adherence to spec: just update. But FK constraint will fail if invalid.
        # Let's check if site exists to give better error if needed, though FK handles it.
        site_check = conn.execute(text("SELECT id FROM sites WHERE id = :sid"), {"sid": new_site_id}).fetchone()
        if not site_check:
             raise HTTPException(status_code=404, detail="New site does not exist")

        # 2. Update the reservation
        update_res_query = """
            UPDATE reservations 
            SET site_id = :new_sid, updated_at = :now
            WHERE id = :rid
        """
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(text(update_res_query), {
            "new_sid": new_site_id,
            "rid": reservation_id,
            "now": now_iso
        })

        # 3. Record change in reservation_changes
        insert_change_query = """
            INSERT INTO reservation_changes (
                reservation_id, 
                changed_by, 
                change_type, 
                old_site_id, 
                new_site_id, 
                created_at
            ) VALUES (
                :rid, 
                :cb, 
                'site_change', 
                :old_sid, 
                :new_sid, 
                :now
            )
        """
        conn.execute(text(insert_change_query), {
            "rid": reservation_id,
            "cb": changed_by,
            "old_sid": old_site_id,
            "new_sid": new_site_id,
            "now": now_iso
        })

    return UpdateSiteResponse(
        message=f"Reservation {reservation_id} site updated successfully.",
        reservation_id=reservation_id,
        old_site_id=old_site_id,
        new_site_id=new_site_id
    )
