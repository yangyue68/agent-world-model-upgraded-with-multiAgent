import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ShippingAddressUpdate(BaseModel):
    street: str = Field(..., description="The street address")
    apartment: str | None = Field(None, description="The apartment or unit number (optional)")
    city: str = Field(..., description="The city")
    state: str = Field(..., description="The state")
    postal_code: str = Field(..., description="The postal code")
    country: str = Field("US", description="The country (defaults to 'US')")


@app.put("/users/{user_id}/shipping-address", operation_id="update_shipping_address")
def update_shipping_address(
    user_id: int = Path(..., description="The ID of the user whose shipping address is being updated"),
    payload: ShippingAddressUpdate = ...,
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID in session")

    # Enforce user scope: only allow updating own address
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Forbidden: cannot update another user's address")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        # Check if user exists
        res = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
        if res.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Find existing default shipping address for this user
        res = conn.execute(
            text("""
                SELECT id FROM addresses 
                WHERE user_id = :uid AND address_type = 'shipping' AND is_default = 1
            """),
            {"uid": user_id}
        )
        row = res.fetchone()

        if row:
            addr_id = row[0]
            # Update existing record
            conn.execute(
                text("""
                    UPDATE addresses 
                    SET street = :street,
                        apartment = :apartment,
                        city = :city,
                        state = :state,
                        postal_code = :postal_code,
                        country = :country,
                        updated_at = :now
                    WHERE id = :id
                """),
                {
                    "id": addr_id,
                    "street": payload.street,
                    "apartment": payload.apartment,
                    "city": payload.city,
                    "state": payload.state,
                    "postal_code": payload.postal_code,
                    "country": payload.country,
                    "now": now,
                }
            )
        else:
            # No default shipping address exists. Insert new one as default.
            result = conn.execute(
                text("""
                    INSERT INTO addresses (
                        user_id, address_type, street, apartment, city, state, postal_code, country, is_default, created_at, updated_at
                    ) VALUES (
                        :uid, 'shipping', :street, :apartment, :city, :state, :postal_code, :country, 1, :now, :now
                    ) RETURNING id
                """),
                {
                    "uid": user_id,
                    "street": payload.street,
                    "apartment": payload.apartment,
                    "city": payload.city,
                    "state": payload.state,
                    "postal_code": payload.postal_code,
                    "country": payload.country,
                    "now": now,
                }
            )
            addr_id = result.fetchone()[0]

        # Fetch the full record to return
        res = conn.execute(
            text("""
                SELECT id, user_id, address_type, street, apartment, city, state, postal_code, country, is_default, created_at, updated_at
                FROM addresses
                WHERE id = :id
            """),
            {"id": addr_id}
        )
        final_row = res.fetchone()

    if not final_row:
        raise HTTPException(status_code=500, detail="Failed to retrieve updated address")

    # Construct dictionary using known column order from the SELECT statement
    columns = [
        "id", "user_id", "address_type", "street", "apartment", 
        "city", "state", "postal_code", "country", "is_default", 
        "created_at", "updated_at"
    ]
    data = dict(zip(columns, final_row))
    
    return data
