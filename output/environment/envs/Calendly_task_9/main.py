import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Calendly Payment Integration API")


# --- Pydantic Models ---

class UpdateEventTypePaymentSettingsRequest(BaseModel):
    price_cents: int = Field(..., description="Price in cents ($99 = 9900)")
    discount_code_id: Optional[int] = Field(None, description="Internal ID of the discount code to apply (e.g., 'WELCOME10')")
    is_paid_only: bool = Field(..., description="Whether the event should be paid-only")


class EventTypeResponse(BaseModel):
    id: int
    title: str
    price_cents: int
    is_paid_only: bool
    discount_code_id: Optional[int]


class DiscountCodeResponse(BaseModel):
    id: int
    user_id: int
    code: str
    discount_percent: float
    is_first_time_only: bool
    max_uses: Optional[int]
    current_uses: int
    expires_at: Optional[str]
    is_active: bool


# --- Endpoints ---

@app.put("/event_types/{event_type_id}", response_model=EventTypeResponse, operation_id="update_event_type_payment_settings")
async def update_event_type_payment_settings(
    event_type_id: int,
    payload: UpdateEventTypePaymentSettingsRequest
):
    """
    Configure the specified event type to require payment via Stripe, set the price to $99, 
    enable the 'WELCOME10' discount code for first-time users, and mark the event as paid-only 
    so free slots are not shown until payment is confirmed.
    
    Note: The database schema does not support storing a direct association between an Event Type
    and a Discount Code. This endpoint updates the Event Type's pricing and visibility settings.
    The `discount_code_id` returned in the response reflects the input parameter but is not persisted
    in the `event_types` table due to schema limitations.
    """
    db = SessionLocal()
    try:
        # Fetch the event type to ensure it exists
        stmt = text("SELECT * FROM event_types WHERE id = :id")
        result = db.execute(stmt, {"id": event_type_id}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Event type not found")

        # Validate discount code if provided
        final_discount_code_id = None
        if payload.discount_code_id is not None:
            dc_stmt = text("SELECT id FROM discount_codes WHERE id = :dc_id AND is_active = 1")
            dc_result = db.execute(dc_stmt, {"dc_id": payload.discount_code_id}).fetchone()
            if not dc_result:
                raise HTTPException(status_code=400, detail="Invalid or inactive discount code ID")
            final_discount_code_id = payload.discount_code_id

        # Update the event type
        # We only update columns that exist in the event_types table: price_cents, is_paid_only
        update_stmt = text("""
            UPDATE event_types 
            SET price_cents = :price_cents, 
                is_paid_only = :is_paid_only, 
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """)
        
        db.execute(update_stmt, {
            "price_cents": payload.price_cents,
            "is_paid_only": 1 if payload.is_paid_only else 0,
            "id": event_type_id
        })
        
        db.commit()

        # Fetch the updated record to return correct state
        fetch_stmt = text("SELECT id, title, price_cents, is_paid_only FROM event_types WHERE id = :id")
        updated_row = db.execute(fetch_stmt, {"id": event_type_id}).fetchone()
        
        if not updated_row:
             raise HTTPException(status_code=500, detail="Failed to retrieve updated event type")

        return EventTypeResponse(
            id=updated_row.id,
            title=updated_row.title,
            price_cents=updated_row.price_cents,
            is_paid_only=bool(updated_row.is_paid_only),
            discount_code_id=final_discount_code_id
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.get("/discount_codes", response_model=DiscountCodeResponse, operation_id="find_discount_code_by_code")
async def find_discount_code_by_code(
    code: str = Query(..., description="Discount code value to search for")
):
    """
    Search for a discount code using its unique code value (e.g., 'WELCOME10') and return its internal ID and details.
    """
    db = SessionLocal()
    try:
        # Select where code matches and is_active=1.
        # Although uniqueness is per user in DB, we assume global lookup for this simplified API contract.
        stmt = text("""
            SELECT id, user_id, code, discount_percent, is_first_time_only, max_uses, current_uses, expires_at, is_active 
            FROM discount_codes 
            WHERE code = :code AND is_active = 1
            LIMIT 1
        """)
        
        row = db.execute(stmt, {"code": code}).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Active discount code '{code}' not found")

        return DiscountCodeResponse(
            id=row.id,
            user_id=row.user_id,
            code=row.code,
            discount_percent=float(row.discount_percent),
            is_first_time_only=bool(row.is_first_time_only),
            max_uses=row.max_uses,
            current_uses=row.current_uses,
            expires_at=str(row.expires_at) if row.expires_at else None,
            is_active=bool(row.is_active)
        )
    finally:
        db.close()
