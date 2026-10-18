import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="ClubStack Loyalty API")


class CustomerResponse(BaseModel):
    id: int
    external_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class EnrollLoyaltyResponse(BaseModel):
    membership_id: int
    transaction_id: int
    points_awarded: int


@app.get("/customers/external-id/{external_id}", response_model=CustomerResponse, operation_id="get_customer_by_external_id")
def get_customer_by_external_id(external_id: str):
    """
    Retrieve customer details by external ID.
    Fetches the internal customer ID and details using the provided external ID.
    """
    db = SessionLocal()
    try:
        stmt = text("""
            SELECT id, external_id, first_name, last_name, email, phone
            FROM customers
            WHERE external_id = :ext_id
        """)
        result = db.execute(stmt, {"ext_id": external_id}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Customer not found")
            
        return CustomerResponse(
            id=result.id,
            external_id=result.external_id,
            first_name=result.first_name,
            last_name=result.last_name,
            email=result.email,
            phone=result.phone
        )
    finally:
        db.close()


@app.post("/customers/{customer_id}/enroll-in-loyalty-program", response_model=EnrollLoyaltyResponse, operation_id="enroll_customer_in_loyalty_program")
def enroll_customer_in_loyalty_program(customer_id: int):
    """
    Enroll a customer in the loyalty program and award welcome points.
    Enrolls the customer with the specified ID in the default loyalty program 
    (assumed to be 'ClubStack Rewards' or the active program with lowest ID) 
    and awards them the welcome points associated with that program.
    """
    db = SessionLocal()
    try:
        # 1. Verify customer exists
        cust_stmt = text("SELECT id FROM customers WHERE id = :cid")
        customer_exists = db.execute(cust_stmt, {"cid": customer_id}).fetchone()
        if not customer_exists:
            raise HTTPException(status_code=404, detail="Customer not found")

        # 2. Find the default active loyalty program
        # Based on schema/data, we look for an active program. 
        # The prompt implies "the" default program. Usually this is the primary one.
        # We'll select the active program with the smallest ID to be deterministic, 
        # assuming ClubStack Rewards (ID 1) is the general/default one.
        prog_stmt = text("""
            SELECT id, welcome_points 
            FROM loyalty_programs 
            WHERE is_active = 1 
            ORDER BY id ASC 
            LIMIT 1
        """)
        program_row = db.execute(prog_stmt).fetchone()
        
        if not program_row:
            raise HTTPException(status_code=500, detail="No active loyalty program available")
        
        program_id = program_row.id
        welcome_points = program_row.welcome_points

        # 3. Check if already enrolled in this specific program
        check_enrollment_stmt = text("""
            SELECT id FROM loyalty_memberships 
            WHERE customer_id = :cid AND program_id = :pid
        """)
        existing_membership = db.execute(check_enrollment_stmt, {"cid": customer_id, "pid": program_id}).fetchone()
        
        if existing_membership:
            raise HTTPException(status_code=409, detail="Customer already enrolled in this loyalty program")

        # 4. Create Membership Record
        insert_membership_stmt = text("""
            INSERT INTO loyalty_memberships (customer_id, program_id, points_balance, status, enrolled_at, created_at, updated_at)
            VALUES (:cid, :pid, :points, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        db.execute(insert_membership_stmt, {
            "cid": customer_id,
            "pid": program_id,
            "points": welcome_points
        })
        
        # Get the new membership ID
        mem_id_stmt = text("SELECT last_insert_rowid()")
        membership_id = db.execute(mem_id_stmt).scalar_one()

        # 5. Create Points Transaction Record
        insert_transaction_stmt = text("""
            INSERT INTO points_transactions (membership_id, points, transaction_type, description, created_at)
            VALUES (:mid, :pts, 'award', 'Welcome bonus', CURRENT_TIMESTAMP)
        """)
        db.execute(insert_transaction_stmt, {
            "mid": membership_id,
            "pts": welcome_points
        })

        # Get the new transaction ID
        trans_id_stmt = text("SELECT last_insert_rowid()")
        transaction_id = db.execute(trans_id_stmt).scalar_one()

        db.commit()

        return EnrollLoyaltyResponse(
            membership_id=membership_id,
            transaction_id=transaction_id,
            points_awarded=welcome_points
        )

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
