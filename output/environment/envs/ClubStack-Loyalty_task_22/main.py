import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

app = FastAPI()


class CouponCreateRequest(BaseModel):
    code: str = Field(..., description="Unique coupon code")
    discount_type: str = Field(..., description="Type of discount ('percentage' or 'fixed')")
    discount_value: float = Field(..., description="Discount amount (percentage or fixed value)")
    valid_until: str = Field(..., description="Expiration date of the coupon in ISO format")


class CouponResponse(BaseModel):
    id: int
    code: str
    discount_type: str
    discount_value: float
    applies_to_first_month: int
    max_redemptions_per_customer: int
    valid_from: str
    valid_until: str
    is_active: int


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string supporting both T...Z and space-separated formats."""
    dt_str = dt_str.strip()
    # Handle Z suffix
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Try common alternative formats
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {dt_str}")


@app.post("/coupons", response_model=CouponResponse, operation_id="create_coupon")
def create_coupon(request: CouponCreateRequest):
    # Validate discount_type against allowed values from schema CHECK constraint
    if request.discount_type not in ("percentage", "fixed"):
        raise HTTPException(
            status_code=400,
            detail="discount_type must be either 'percentage' or 'fixed'"
        )
    
    # Validate discount_value > 0 per schema CHECK constraint
    if request.discount_value <= 0:
        raise HTTPException(
            status_code=400,
            detail="discount_value must be greater than 0"
        )
    
    # Parse valid_until
    try:
        valid_until_dt = parse_iso_datetime(request.valid_until)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Format datetimes for SQLite storage
    valid_until_str = valid_until_dt.strftime("%Y-%m-%d %H:%M:%S")
    
    db = SessionLocal()
    try:
        # Check if coupon code already exists
        existing = db.execute(
            text("SELECT id FROM coupons WHERE code = :code"),
            {"code": request.code}
        ).fetchone()
        
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Coupon with code '{request.code}' already exists"
            )
        
        # Insert new coupon with task-specific defaults:
        # - applies_to_first_month = 1 (first month's subscription)
        # - max_redemptions_per_customer = 1 (one-time use per customer)
        # - is_active = 1
        # - valid_from = current timestamp
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        result = db.execute(
            text("""
                INSERT INTO coupons 
                (code, discount_type, discount_value, applies_to_first_month, 
                 max_redemptions_per_customer, valid_from, valid_until, is_active, 
                 created_at, updated_at)
                VALUES 
                (:code, :discount_type, :discount_value, :applies_to_first_month,
                 :max_redemptions_per_customer, :valid_from, :valid_until, :is_active,
                 :created_at, :updated_at)
            """),
            {
                "code": request.code,
                "discount_type": request.discount_type,
                "discount_value": request.discount_value,
                "applies_to_first_month": 1,
                "max_redemptions_per_customer": 1,
                "valid_from": now_str,
                "valid_until": valid_until_str,
                "is_active": 1,
                "created_at": now_str,
                "updated_at": now_str,
            }
        )
        
        coupon_id = result.lastrowid
        
        # Get all distinct plan tiers from plans table to associate with this coupon
        # The task says "for any plan tier", so we link to all existing tiers
        tiers_result = db.execute(
            text("SELECT DISTINCT tier FROM plans ORDER BY tier")
        )
        tiers = [row[0] for row in tiers_result.fetchall()]
        
        for tier in tiers:
            db.execute(
                text("""
                    INSERT INTO coupon_plan_tiers (coupon_id, plan_tier)
                    VALUES (:coupon_id, :plan_tier)
                """),
                {"coupon_id": coupon_id, "plan_tier": tier}
            )
        
        db.commit()
        
        # Fetch the created coupon to return full details
        coupon_row = db.execute(
            text("""
                SELECT id, code, discount_type, discount_value, applies_to_first_month,
                       max_redemptions_per_customer, valid_from, valid_until, is_active
                FROM coupons
                WHERE id = :id
            """),
            {"id": coupon_id}
        ).fetchone()
        
        if not coupon_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created coupon")
        
        return CouponResponse(
            id=coupon_row.id,
            code=coupon_row.code,
            discount_type=coupon_row.discount_type,
            discount_value=float(coupon_row.discount_value),
            applies_to_first_month=int(coupon_row.applies_to_first_month),
            max_redemptions_per_customer=int(coupon_row.max_redemptions_per_customer),
            valid_from=str(coupon_row.valid_from),
            valid_until=str(coupon_row.valid_until),
            is_active=int(coupon_row.is_active),
        )
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        db.close()
