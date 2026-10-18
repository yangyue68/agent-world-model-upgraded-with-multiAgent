import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CouponResponse(BaseModel):
    id: int
    code: str
    is_active: int


@app.put("/coupons/{id}", response_model=CouponResponse, operation_id="deactivate_coupon", tags=["PrimaryResource"])
def deactivate_coupon(id: int):
    """
    Deactivate the coupon with the specified ID so it can no longer be redeemed.
    This action sets the is_active flag to 0 for the coupon.
    """
    # Fetch current state to ensure existence and get code for response
    select_stmt = text("SELECT id, code, is_active FROM coupons WHERE id = :coupon_id")
    
    with engine.connect() as conn:
        result = conn.execute(select_stmt, {"coupon_id": id})
        row = result.fetchone()
        
        if row is None:
            raise HTTPException(status_code=404, detail=f"Coupon with ID {id} not found")
        
        current_id, code, current_is_active = row
        
        # Update the coupon to inactive
        update_stmt = text("UPDATE coupons SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = :coupon_id")
        conn.execute(update_stmt, {"coupon_id": id})
        conn.commit()
        
        return CouponResponse(
            id=current_id,
            code=code,
            is_active=0
        )
