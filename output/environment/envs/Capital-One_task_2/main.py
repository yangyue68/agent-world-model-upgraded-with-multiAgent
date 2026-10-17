import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CreditCardApplicationRequest(BaseModel):
    credit_card_name: str
    requested_credit_limit: float


class CreditCardApplicationResponse(BaseModel):
    application_id: int
    status: str


class AuthorizedUserRequest(BaseModel):
    application_id: int
    first_name: str
    last_name: str
    email: str
    date_of_birth: str


class AuthorizedUserResponse(BaseModel):
    authorized_user_id: int


@app.post("/credit-card-applications", response_model=CreditCardApplicationResponse, operation_id="apply_for_credit_card")
def apply_for_credit_card(payload: CreditCardApplicationRequest, db=Depends(get_db)):
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: No user context found")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid user context")

    # Verify user exists
    user_check = db.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": current_user_id}).fetchone()
    if not user_check:
        raise HTTPException(status_code=404, detail="User not found")

    # Find credit card by name
    card_row = db.execute(
        text("SELECT id FROM credit_cards WHERE card_name = :name"),
        {"name": payload.credit_card_name}
    ).fetchone()
    
    if not card_row:
        raise HTTPException(status_code=404, detail=f"Credit card '{payload.credit_card_name}' not found")
    
    credit_card_id = card_row[0]

    # Validate requested credit limit > 0 (handled by DB constraint, but good to check early for clear error)
    if payload.requested_credit_limit <= 0:
        raise HTTPException(status_code=422, detail="Requested credit limit must be greater than 0")

    # Insert new application
    result = db.execute(
        text("""
            INSERT INTO credit_card_applications (user_id, credit_card_id, requested_credit_limit, status)
            VALUES (:uid, :cid, :limit, 'pending')
        """),
        {
            "uid": current_user_id,
            "cid": credit_card_id,
            "limit": payload.requested_credit_limit
        }
    )
    db.commit()
    
    application_id = result.lastrowid
    
    # Fetch the inserted row to confirm status
    app_row = db.execute(
        text("SELECT status FROM credit_card_applications WHERE id = :aid"),
        {"aid": application_id}
    ).fetchone()
    
    status = app_row[0] if app_row else "pending"

    return CreditCardApplicationResponse(application_id=application_id, status=status)


@app.post("/authorized-users", response_model=AuthorizedUserResponse, operation_id="add_authorized_user")
def add_authorized_user(payload: AuthorizedUserRequest, db=Depends(get_db)):
    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: No user context found")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid user context")

    # Verify the application exists and belongs to the current user
    app_check = db.execute(
        text("SELECT id, user_id FROM credit_card_applications WHERE id = :aid"),
        {"aid": payload.application_id}
    ).fetchone()
    
    if not app_check:
        raise HTTPException(status_code=404, detail="Credit card application not found")
    
    owner_id = app_check[1]
    if owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Forbidden: You do not own this application")

    # Insert authorized user with full access level as per task requirement
    result = db.execute(
        text("""
            INSERT INTO authorized_users (application_id, first_name, last_name, email, date_of_birth, access_level)
            VALUES (:aid, :fn, :ln, :em, :dob, 'full')
        """),
        {
            "aid": payload.application_id,
            "fn": payload.first_name,
            "ln": payload.last_name,
            "em": payload.email,
            "dob": payload.date_of_birth
        }
    )
    db.commit()
    
    authorized_user_id = result.lastrowid

    return AuthorizedUserResponse(authorized_user_id=authorized_user_id)
