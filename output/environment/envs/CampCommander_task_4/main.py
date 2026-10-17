import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class CancelReservationRequest(BaseModel):
    cancelled_by: int
    reason: Optional[str] = None


class ReservationDetailsResponse(BaseModel):
    id: int
    reservation_code: str
    guest_id: int
    campsite_id: int
    check_in: date
    check_out: date
    status: str
    total_amount: float
    deposit_amount: float
    created_by: int
    created_at: datetime
    updated_at: datetime
    guest_full_name: str
    campsite_name: str
    campsite_type: str
    campsite_capacity: int
    campsite_nightly_rate: float


class CancellationPolicyResponse(BaseModel):
    id: int
    name: str
    description: str
    refund_percentage: float
    days_before_check_in: int
    is_active: bool


class CancelReservationResponse(BaseModel):
    reservation_id: int
    cancellation_id: int
    refund_amount: float
    refund_status: str
    policy_used: str
    policy_description: str


@app.get("/reservations/{reservation_code}", response_model=ReservationDetailsResponse, operation_id="get_reservation_details")
def get_reservation_details(reservation_code: str = Path(...)):
    with SessionLocal() as db:
        query = text("""
            SELECT 
                r.id,
                r.reservation_code,
                r.guest_id,
                r.campsite_id,
                r.check_in,
                r.check_out,
                r.status,
                r.total_amount,
                r.deposit_amount,
                r.created_by,
                r.created_at,
                r.updated_at,
                g.full_name AS guest_full_name,
                c.name AS campsite_name,
                c.site_type AS campsite_type,
                c.capacity AS campsite_capacity,
                c.nightly_rate AS campsite_nightly_rate
            FROM reservations r
            JOIN guests g ON r.guest_id = g.id
            JOIN campsites c ON r.campsite_id = c.id
            WHERE r.reservation_code = :code
        """)
        result = db.execute(query, {"code": reservation_code}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Reservation not found")
            
        return {
            "id": result[0],
            "reservation_code": result[1],
            "guest_id": result[2],
            "campsite_id": result[3],
            "check_in": result[4],
            "check_out": result[5],
            "status": result[6],
            "total_amount": result[7],
            "deposit_amount": result[8],
            "created_by": result[9],
            "created_at": result[10],
            "updated_at": result[11],
            "guest_full_name": result[12],
            "campsite_name": result[13],
            "campsite_type": result[14],
            "campsite_capacity": result[15],
            "campsite_nightly_rate": result[16],
        }


@app.get("/cancellation_policies", response_model=list[CancellationPolicyResponse], operation_id="list_cancellation_policies")
def list_cancellation_policies():
    with SessionLocal() as db:
        query = text("""
            SELECT id, name, description, refund_percentage, days_before_check_in, is_active
            FROM cancellation_policies
            WHERE is_active = 1
            ORDER BY days_before_check_in DESC
        """)
        results = db.execute(query).fetchall()
        
        policies = []
        for row in results:
            policies.append({
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "refund_percentage": row[3],
                "days_before_check_in": row[4],
                "is_active": bool(row[5]),
            })
        return policies


@app.put("/reservations/{reservation_code}/cancel", response_model=CancelReservationResponse, operation_id="cancel_reservation")
def cancel_reservation(
    reservation_code: str = Path(...),
    payload: CancelReservationRequest = Body(...)
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with SessionLocal() as db:
        # 1. Fetch reservation
        res_query = text("""
            SELECT id, guest_id, campsite_id, check_in, status, deposit_amount
            FROM reservations
            WHERE reservation_code = :code
        """)
        res_row = db.execute(res_query, {"code": reservation_code}).fetchone()
        
        if not res_row:
            raise HTTPException(status_code=404, detail="Reservation not found")
            
        reservation_id = res_row[0]
        check_in_date = res_row[3]
        status = res_row[4]
        deposit_amount = res_row[5]
        
        if status != 'confirmed':
            raise HTTPException(status_code=400, detail=f"Cannot cancel reservation with status '{status}'")
            
        # Check if already cancelled
        existing_cancel_query = text("SELECT id FROM cancellations WHERE reservation_id = :rid")
        if db.execute(existing_cancel_query, {"rid": reservation_id}).fetchone():
            raise HTTPException(status_code=400, detail="Reservation already cancelled")

        # 2. Determine Policy
        # Calculate days difference between today and check_in
        today = date.today()
        # Ensure check_in_date is a date object
        if isinstance(check_in_date, str):
            check_in_obj = datetime.strptime(check_in_date, "%Y-%m-%d").date()
        elif isinstance(check_in_date, datetime):
            check_in_obj = check_in_date.date()
        else:
            check_in_obj = check_in_date
            
        days_diff = (check_in_obj - today).days
        
        # Find applicable active policies sorted by days_before_check_in descending (most generous first)
        # The condition should be: days_before_check_in <= days_diff
        # If days_diff is negative (late cancellation), we need to find the strictest policy that applies.
        # Usually, strictest policies have lower days_before_check_in requirements (e.g., 0 or less than X).
        # However, looking at the data:
        # Flexible: 7 days before -> requires >= 7 days left
        # Standard: 3 days before -> requires >= 3 days left
        # Strict: 0 days before -> requires >= 0 days left? Or just default fallback?
        # The SQL `WHERE ... AND days_before_check_in <= :days` works correctly if :days is positive.
        # If :days is negative (past check-in), no policy with non-negative days_before_check_in will match `<= negative`.
        # We must handle the case where the cancellation happens after check-in or very close to it.
        # Typically, if days_diff < min(days_before_check_in of all policies), we fall back to the strictest one (lowest days_before_check_in).
        # Let's fetch all active policies ordered by days_before_check_in ASC (strictest first) to pick the best fit if none match strictly,
        # OR adjust the logic to ensure we always pick a policy.
        
        # Correct Logic:
        # A policy applies if the number of days remaining until check-in is greater than or equal to the policy's requirement.
        # i.e., days_diff >= policy.days_before_check_in.
        # Wait, the previous code used `days_before_check_in <= :days`. This implies `policy_req <= actual_days_left`.
        # Example: Actual days left = 10. Policy req = 7. 7 <= 10 is True. Matches.
        # Example: Actual days left = 2. Policy req = 7. 7 <= 2 is False. No match.
        # Example: Actual days left = 2. Policy req = 3. 3 <= 2 is False. No match.
        # Example: Actual days left = 2. Policy req = 0. 0 <= 2 is True. Matches.
        
        # So if days_diff is negative (e.g., -5), then:
        # 7 <= -5 (False)
        # 3 <= -5 (False)
        # 0 <= -5 (False)
        # Result: No policy found.
        
        # To fix this, if no policy matches the strict criteria (which handles future dates well),
        # we should select the most restrictive policy available (usually the one with lowest days_before_check_in)
        # assuming it covers late/no-show scenarios or simply defaults to the strictest option defined.
        # Alternatively, change the query to order by relevance and take the top one that fits, 
        # but since SQLite doesn't support complex window functions easily in raw text without care,
        # let's implement a fallback: if no policy matches `days_before_check_in <= days_diff`,
        # pick the policy with the smallest `days_before_check_in` among active ones.
        
        pol_query = text("""
            SELECT id, name, description, refund_percentage, days_before_check_in
            FROM cancellation_policies
            WHERE is_active = 1 AND days_before_check_in <= :days
            ORDER BY days_before_check_in DESC
            LIMIT 1
        """)
        
        policy_row = db.execute(pol_query, {"days": days_diff}).fetchone()
        
        if not policy_row:
            # Fallback: Get the strictest policy (lowest days_before_check_in)
            fallback_pol_query = text("""
                SELECT id, name, description, refund_percentage, days_before_check_in
                FROM cancellation_policies
                WHERE is_active = 1
                ORDER BY days_before_check_in ASC
                LIMIT 1
            """)
            policy_row = db.execute(fallback_pol_query).fetchone()
            
            if not policy_row:
                 raise HTTPException(status_code=500, detail="No active cancellation policies configured")

        policy_id = policy_row[0]
        policy_name = policy_row[1]
        policy_desc = policy_row[2]
        refund_pct = policy_row[3]
        
        refund_amount = round(deposit_amount * (refund_pct / 100.0), 2)
        
        # 3. Process Refund Payment Record
        # Find original payment method for the deposit
        pay_query = text("""
            SELECT payment_method
            FROM payments
            WHERE reservation_id = :rid AND payment_type = 'deposit' AND status = 'completed'
            ORDER BY created_at ASC
            LIMIT 1
        """)
        pay_row = db.execute(pay_query, {"rid": reservation_id}).fetchone()
        
        if not pay_row:
             # Fallback or error? Assuming deposit exists if reservation confirmed.
             raise HTTPException(status_code=400, detail="Original deposit payment not found")
             
        original_payment_method = pay_row[0]
        
        # Insert new refund payment
        insert_pay_query = text("""
            INSERT INTO payments (reservation_id, amount, payment_method, payment_type, status, transaction_ref, created_at, updated_at)
            VALUES (:rid, :amt, :method, 'refund', 'completed', :txn_ref, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
        """)
        txn_ref = f"REF-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        new_payment_id = db.execute(insert_pay_query, {
            "rid": reservation_id,
            "amt": refund_amount,
            "method": original_payment_method,
            "txn_ref": txn_ref
        }).scalar_one()
        
        # Mark original deposit as refunded
        update_orig_pay_query = text("""
            UPDATE payments
            SET status = 'refunded', updated_at = CURRENT_TIMESTAMP
            WHERE reservation_id = :rid AND payment_type = 'deposit' AND status = 'completed'
        """)
        db.execute(update_orig_pay_query, {"rid": reservation_id})

        # 4. Create Cancellation Record
        insert_cancel_query = text("""
            INSERT INTO cancellations (reservation_id, cancelled_by, cancellation_date, reason, policy_id, refund_amount, refund_status, refund_payment_id, created_at, updated_at)
            VALUES (:rid, :uid, CURRENT_TIMESTAMP, :reason, :pid, :ramt, 'processed', :rpid, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
        """)
        cancellation_id = db.execute(insert_cancel_query, {
            "rid": reservation_id,
            "uid": current_user_id,
            "reason": payload.reason,
            "pid": policy_id,
            "ramt": refund_amount,
            "rpid": new_payment_id
        }).scalar_one()
        
        # 5. Update Reservation Status
        update_res_query = text("""
            UPDATE reservations
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE id = :rid
        """)
        db.execute(update_res_query, {"rid": reservation_id})
        
        db.commit()
        
        return {
            "reservation_id": reservation_id,
            "cancellation_id": cancellation_id,
            "refund_amount": refund_amount,
            "refund_status": "processed",
            "policy_used": policy_name,
            "policy_description": policy_desc
        }
