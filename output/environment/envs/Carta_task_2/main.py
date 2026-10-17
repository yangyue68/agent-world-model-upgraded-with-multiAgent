import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ExerciseRequest(BaseModel):
    employee_id: int
    stock_grant_id: int
    options_exercised: int
    payment_method: str = Field(..., description="Payment method used for exercise (wire, ach, check, credit_card, other)")
    payment_reference: Optional[str] = None


class UpdatedGrant(BaseModel):
    id: int
    vested_options: int
    exercised_options: int
    remaining_options: int
    status: str


class ExerciseResponse(BaseModel):
    exercise_id: int
    payment_id: int
    issuance_id: int
    updated_grant: UpdatedGrant


@app.post("/stock-options/exercise", response_model=ExerciseResponse, operation_id="exercise_stock_options")
def exercise_stock_options(req: ExerciseRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: No user context found")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with engine.begin() as conn:
        # 1. Validate Employee exists and get their User ID for payments
        emp_row = conn.execute(
            text("SELECT id, user_id FROM employees WHERE id = :emp_id"),
            {"emp_id": req.employee_id}
        ).fetchone()
        
        if not emp_row:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        employee_db_id = emp_row[0]
        employee_user_id = emp_row[1]

        # 2. Validate Stock Grant exists, belongs to employee, and has sufficient vested options
        grant_row = conn.execute(
            text("""
                SELECT id, employee_id, total_options, exercise_price, vested_options, 
                       exercised_options, remaining_options, status
                FROM stock_grants 
                WHERE id = :grant_id AND employee_id = :emp_id
            """),
            {"grant_id": req.stock_grant_id, "emp_id": employee_db_id}
        ).fetchone()

        if not grant_row:
            raise HTTPException(status_code=404, detail="Stock grant not found or does not belong to employee")

        grant_id = grant_row[0]
        grant_employee_id = grant_row[1]
        total_options = grant_row[2]
        exercise_price = grant_row[3]
        vested_options = grant_row[4]
        current_exercised_options = grant_row[5]
        current_remaining_options = grant_row[6]
        current_status = grant_row[7]

        # Check if grant is active/cancellable etc? Usually only active grants can be exercised partially/fully
        if current_status not in ('active', 'partially_exercised'):
             raise HTTPException(status_code=400, detail=f"Cannot exercise options from a grant with status '{current_status}'")

        if req.options_exercised <= 0:
            raise HTTPException(status_code=400, detail="Options exercised must be greater than zero")

        if req.options_exercised > vested_options:
            raise HTTPException(status_code=400, detail=f"Not enough vested options. Vested: {vested_options}, Requested: {req.options_exercised}")

        # Calculate new states
        new_vested_options = vested_options - req.options_exercised
        new_exercised_options = current_exercised_options + req.options_exercised
        new_remaining_options = total_options - new_exercised_options
        
        # Determine new status based on remaining vs total
        # If all options are exercised (remaining == 0), status becomes fully_exercised
        # If some but not all are exercised, it remains active or becomes partially_exercised?
        # The schema allows 'active' and 'partially_exercised'. 
        # Typically, once you start exercising, it might become 'partially_exercised' until fully done.
        # Let's look at the initial data: Status is 'active' even with 0 exercised.
        # If we exercise some, let's mark it 'partially_exercised' unless it's fully exhausted.
        
        if new_remaining_options == 0:
            new_status = 'fully_exercised'
        elif new_exercised_options > 0:
            new_status = 'partially_exercised'
        else:
            new_status = 'active'

        today = date.today().isoformat()
        now_dt = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # 3. Create Option Exercise Record
        total_cost = req.options_exercised * exercise_price
        
        res_exercise = conn.execute(
            text("""
                INSERT INTO option_exercises (
                    stock_grant_id, employee_id, exercise_date, options_exercised, 
                    exercise_price_per_share, total_exercise_cost, payment_status, 
                    payment_date, payment_reference, shares_issued, status, created_at, updated_at
                ) VALUES (
                    :sgid, :eid, :exdate, :opts, :price, :cost, 'paid', :paydate, :pref, :shares, 'completed', :created, :updated
                )
            """),
            {
                "sgid": grant_id,
                "eid": employee_db_id,
                "exdate": today,
                "opts": req.options_exercised,
                "price": exercise_price,
                "cost": total_cost,
                "paydate": today,
                "pref": req.payment_reference,
                "shares": req.options_exercised,
                "created": now_dt,
                "updated": now_dt
            }
        )
        exercise_id = res_exercise.lastrowid

        # 4. Create Payment Record
        # Payer ID should be the user associated with the employee
        ref_num = req.payment_reference
        if not ref_num:
            # Generate a unique reference if none provided? Or allow null? Schema says UNIQUE.
            # If multiple exercises happen without refs, they will collide if we don't generate one.
            # However, usually clients provide this. If optional, maybe we skip inserting into payments if no ref?
            # But the task says "record the $10,000 payment". So we must insert.
            # To ensure uniqueness, we could append timestamp/id, but simpler is to require it or handle collision.
            # Given the spec says `payment_reference` is optional in request, but DB requires UNIQUE.
            # We'll assume if it's missing, we generate a deterministic-ish one or just fail if duplicate.
            # For robustness in this simulation, let's make one up if missing using exercise_id.
            ref_num = f"AUTO-{exercise_id}"

        res_payment = conn.execute(
            text("""
                INSERT INTO payments (
                    option_exercise_id, payer_id, amount, currency, payment_method, 
                    payment_date, reference_number, status, created_at, updated_at
                ) VALUES (
                    :oeid, :pid, :amt, 'USD', :pmethod, :pdate, :ref, 'completed', :created, :updated
                )
            """),
            {
                "oeid": exercise_id,
                "pid": employee_user_id,
                "amt": total_cost,
                "pmethod": req.payment_method,
                "pdate": today,
                "ref": ref_num,
                "created": now_dt,
                "updated": now_dt
            }
        )
        payment_id = res_payment.lastrowid

        # 5. Create Share Issuance Record
        cert_num = f"CERT-{today.replace('-', '')}-{exercise_id}"
        
        res_issuance = conn.execute(
            text("""
                INSERT INTO share_issuances (
                    option_exercise_id, employee_id, share_class, shares_issued, 
                    issue_date, certificate_number, status, created_at, updated_at
                ) VALUES (
                    :oeid, :eid, 'common', :shares, :idate, :cert, 'issued', :created, :updated
                )
            """),
            {
                "oeid": exercise_id,
                "eid": employee_db_id,
                "shares": req.options_exercised,
                "idate": today,
                "cert": cert_num,
                "created": now_dt,
                "updated": now_dt
            }
        )
        issuance_id = res_issuance.lastrowid

        # 6. Update Stock Grant
        conn.execute(
            text("""
                UPDATE stock_grants 
                SET vested_options = :new_vested, 
                    exercised_options = :new_exercised, 
                    remaining_options = :new_remaining, 
                    status = :new_status,
                    updated_at = :updated
                WHERE id = :gid
            """),
            {
                "new_vested": new_vested_options,
                "new_exercised": new_exercised_options,
                "new_remaining": new_remaining_options,
                "new_status": new_status,
                "updated": now_dt,
                "gid": grant_id
            }
        )

        # 7. Audit Log
        conn.execute(
            text("""
                INSERT INTO audit_logs (user_id, action, entity_type, entity_id, details, created_at)
                VALUES (:uid, :action, :etype, :eid, :details, :created)
            """),
            {
                "uid": current_user_id,
                "action": "EXERCISE_OPTIONS",
                "etype": "option_exercise",
                "eid": exercise_id,
                "details": f"Exercised {req.options_exercised} options from grant {grant_id}",
                "created": now_dt
            }
        )

    return ExerciseResponse(
        exercise_id=exercise_id,
        payment_id=payment_id,
        issuance_id=issuance_id,
        updated_grant=UpdatedGrant(
            id=grant_id,
            vested_options=new_vested_options,
            exercised_options=new_exercised_options,
            remaining_options=new_remaining_options,
            status=new_status
        )
    )
