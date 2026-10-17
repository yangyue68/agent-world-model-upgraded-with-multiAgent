import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CampCommander API")


# --- Pydantic Models for Responses ---

class GuestResponse(BaseModel):
    id: int
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None

    class Config:
        from_attributes = True


class PaymentMethodResponse(BaseModel):
    id: int
    card_type: str
    last_four: str
    expiry_month: int
    expiry_year: int
    is_default: bool

    class Config:
        from_attributes = True


class ReservationResponse(BaseModel):
    id: int
    confirmation_number: str
    guest_id: int
    user_id: int
    check_in_date: date
    check_out_date: date
    payment_method_id: Optional[int] = None
    status: str

    class Config:
        from_attributes = True


class UpdateReservationRequest(BaseModel):
    new_check_out_date: date
    new_payment_method_id: int


class MessageResponse(BaseModel):
    message: str


# --- Endpoints ---

@app.get("/guests/{full_name}", response_model=GuestResponse, operation_id="get_guest_by_name")
def get_guest_by_name(full_name: str = Path(..., description="Full name of the guest")):
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, full_name, email, phone FROM guests WHERE full_name = :name"),
            {"name": full_name}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Guest not found")
            
        return GuestResponse(**result._asdict())
    finally:
        db.close()


@app.get("/payment_methods", response_model=list[PaymentMethodResponse], operation_id="list_payment_methods")
def list_payment_methods(user_id: int = Query(..., description="ID of the user whose payment methods are to be retrieved")):
    db = SessionLocal()
    try:
        results = db.execute(
            text("""
                SELECT id, card_type, last_four, expiry_month, expiry_year, 
                       CASE WHEN is_default = 1 THEN 1 ELSE 0 END as is_default_int
                FROM payment_methods 
                WHERE user_id = :uid
            """),
            {"uid": user_id}
        ).fetchall()
        
        items = []
        for row in results:
            items.append(PaymentMethodResponse(
                id=row.id,
                card_type=row.card_type,
                last_four=row.last_four,
                expiry_month=row.expiry_month,
                expiry_year=row.expiry_year,
                is_default=bool(row.is_default_int)
            ))
        return items
    finally:
        db.close()


@app.get("/reservations/{confirmation_number}", response_model=ReservationResponse, operation_id="get_reservation_by_confirmation")
def get_reservation_by_confirmation(confirmation_number: str = Path(..., description="Unique identifier for the reservation")):
    db = SessionLocal()
    try:
        result = db.execute(
            text("""
                SELECT id, confirmation_number, guest_id, user_id, check_in_date, check_out_date, payment_method_id, status
                FROM reservations 
                WHERE confirmation_number = :conf_num
            """),
            {"conf_num": confirmation_number}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Reservation not found")
            
        return ReservationResponse(**result._asdict())
    finally:
        db.close()


@app.put("/reservations/{id}", response_model=MessageResponse, operation_id="update_reservation")
def update_reservation(
    id: int = Path(..., description="Internal ID of the reservation to update"),
    body: UpdateReservationRequest = ...
):
    db = SessionLocal()
    try:
        # Get current actor from environment
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id_str:
             raise HTTPException(status_code=401, detail="Unauthorized: No user context provided")
        
        try:
            current_user_id = int(current_user_id_str)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid user context format")

        # Fetch existing reservation
        res_row = db.execute(
            text("SELECT * FROM reservations WHERE id = :rid"),
            {"rid": id}
        ).fetchone()
        
        if not res_row:
            raise HTTPException(status_code=404, detail="Reservation not found")
        
        old_check_out_date = res_row.check_out_date
        old_payment_method_id = res_row.payment_method_id
        
        # Parse check_in_date from string to date object if necessary
        # SQLite returns DATE columns as strings unless configured otherwise or using specific types
        check_in_date_val = res_row.check_in_date
        if isinstance(check_in_date_val, str):
            try:
                check_in_date_obj = date.fromisoformat(check_in_date_val.split(' ')[0])
            except ValueError:
                 # Fallback for other formats if needed, though ISO is standard for DATE type in this context usually
                 check_in_date_obj = datetime.strptime(check_in_date_val.split(' ')[0], "%Y-%m-%d").date()
        else:
            check_in_date_obj = check_in_date_val
            
        # Validate new dates logically
        if body.new_check_out_date <= check_in_date_obj:
            raise HTTPException(status_code=400, detail="Check-out date must be after check-in date")

        # Verify payment method exists and belongs to the same user associated with the reservation
        pm_row = db.execute(
            text("SELECT id FROM payment_methods WHERE id = :pm_id AND user_id = :u_id"),
            {"pm_id": body.new_payment_method_id, "u_id": res_row.user_id}
        ).fetchone()
        
        if not pm_row:
            raise HTTPException(status_code=400, detail="Invalid payment method or does not belong to this user")

        # Perform Update
        db.execute(
            text("""
                UPDATE reservations 
                SET check_out_date = :new_co, 
                    payment_method_id = :new_pm,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :rid
            """),
            {
                "new_co": body.new_check_out_date.isoformat(),
                "new_pm": body.new_payment_method_id,
                "rid": id
            }
        )
        
        # Prepare old_check_out_date for audit log insertion
        # Ensure it's a string in ISO format for consistency if it was returned as something else, 
        # but typically we just pass what we have. The DB expects DATE/TEXT compatible value.
        old_co_str = old_check_out_date
        if isinstance(old_check_out_date, date):
            old_co_str = old_check_out_date.isoformat()
        elif isinstance(old_check_out_date, datetime):
            old_co_str = old_check_out_date.date().isoformat()
        elif not isinstance(old_check_out_date, str):
            old_co_str = str(old_check_out_date)

        # Log Audit
        db.execute(
            text("""
                INSERT INTO reservation_audit_log 
                (reservation_id, user_id, action, old_check_out_date, new_check_out_date, old_payment_method_id, new_payment_method_id, notes)
                VALUES (:r_id, :u_id, 'updated', :old_co, :new_co, :old_pm, :new_pm, :notes)
            """),
            {
                "r_id": id,
                "u_id": current_user_id,
                "old_co": old_co_str,
                "new_co": body.new_check_out_date.isoformat(),
                "old_pm": old_payment_method_id,
                "new_pm": body.new_payment_method_id,
                "notes": f"Extended stay and changed payment method by user {current_user_id}"
            }
        )
        
        db.commit()
        
        return MessageResponse(message=f"Reservation {id} updated successfully.")
    
    except HTTPException:
        # Re-raise HTTP exceptions so they are returned with their original status codes
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
