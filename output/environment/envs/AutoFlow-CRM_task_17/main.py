import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CustomerCreateRequest(BaseModel):
    name: str = Field(..., description="Full name of the customer")
    phone: str = Field(..., description="Phone number of the customer")
    email: str = Field(..., description="Email address of the customer")
    address: str = Field(..., description="Physical address of the customer")
    is_repeat_customer: bool = Field(
        ..., description="Flag indicating if the customer is a repeat customer"
    )


class CustomerResponse(BaseModel):
    id: int
    name: str
    phone: str
    email: str
    address: str
    is_repeat_customer: bool
    created_by: int
    created_at: str
    updated_at: str


@app.post("/customers", operation_id="create_customer", response_model=CustomerResponse)
def create_customer(request: CustomerCreateRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user ID in environment")

    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    # Verify that the user exists
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM users WHERE id = :user_id"),
            {"user_id": current_user_id},
        ).fetchone()
        if result is None:
            raise HTTPException(status_code=401, detail="User not found")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        insert_result = conn.execute(
            text(
                """
                INSERT INTO customers (name, phone, email, address, is_repeat_customer, created_by, created_at, updated_at)
                VALUES (:name, :phone, :email, :address, :is_repeat_customer, :created_by, :created_at, :updated_at)
                """
            ),
            {
                "name": request.name,
                "phone": request.phone,
                "email": request.email,
                "address": request.address,
                "is_repeat_customer": 1 if request.is_repeat_customer else 0,
                "created_by": current_user_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        new_id = insert_result.lastrowid

        row = conn.execute(
            text(
                """
                SELECT id, name, phone, email, address, is_repeat_customer, created_by, created_at, updated_at
                FROM customers WHERE id = :id
                """
            ),
            {"id": new_id},
        ).fetchone()

    return CustomerResponse(
        id=row[0],
        name=row[1],
        phone=row[2],
        email=row[3],
        address=row[4],
        is_repeat_customer=bool(row[5]),
        created_by=row[6],
        created_at=str(row[7]),
        updated_at=str(row[8]),
    )
