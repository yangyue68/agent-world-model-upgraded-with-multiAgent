import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")  # This will be replaced by actual DATABASE_PATH in runtime

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class CreateListingRequest(BaseModel):
    vehicle_vin: str = Field(..., description="VIN of the vehicle to list")
    lot_name: str = Field(..., description="Name of the lot where the vehicle will be listed")
    mileage: int = Field(..., ge=0, description="Mileage of the vehicle")
    condition: str = Field(..., description="Condition of the vehicle ('New', 'Used', 'Certified Pre-Owned')")
    price: float = Field(..., ge=0, description="Listing price of the vehicle")
    photo_count: int = Field(..., ge=0, description="Number of photos associated with the listing")


class ListingResponse(BaseModel):
    id: int
    vehicle_id: int
    lot_id: int
    user_id: int
    mileage: int
    condition: str
    price: float
    photo_count: int
    created_at: str
    updated_at: str


app = FastAPI(title="AutoBridge Dealer API", version="1.0.0")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/listings", response_model=ListingResponse, operation_id="create_vehicle_listing", tags=["PrimaryResource"])
def create_vehicle_listing(request: CreateListingRequest):
    """Create a new vehicle listing."""
    
    # Validate condition against allowed values
    allowed_conditions = ["New", "Used", "Certified Pre-Owned"]
    if request.condition not in allowed_conditions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid condition. Must be one of: {', '.join(allowed_conditions)}"
        )
    
    # Get current user ID from environment
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User authentication required"
        )
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID format"
        )
    
    db = SessionLocal()
    try:
        # Look up vehicle by VIN
        vehicle_result = db.execute(
            text("SELECT id FROM vehicles WHERE vin = :vin"),
            {"vin": request.vehicle_vin}
        ).fetchone()
        
        if not vehicle_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Vehicle with VIN '{request.vehicle_vin}' not found"
            )
        
        vehicle_id = vehicle_result[0]
        
        # Look up lot by name
        lot_result = db.execute(
            text("SELECT id FROM lots WHERE name = :name"),
            {"name": request.lot_name}
        ).fetchone()
        
        if not lot_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Lot with name '{request.lot_name}' not found"
            )
        
        lot_id = lot_result[0]
        
        # Verify user exists
        user_result = db.execute(
            text("SELECT id FROM users WHERE id = :user_id"),
            {"user_id": user_id}
        ).fetchone()
        
        if not user_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID '{user_id}' not found"
            )
        
        # Insert new listing
        now = datetime.utcnow().isoformat() + "Z"
        
        insert_result = db.execute(
            text("""
                INSERT INTO listings (vehicle_id, lot_id, user_id, mileage, condition, price, photo_count, created_at, updated_at)
                VALUES (:vehicle_id, :lot_id, :user_id, :mileage, :condition, :price, :photo_count, :created_at, :updated_at)
            """),
            {
                "vehicle_id": vehicle_id,
                "lot_id": lot_id,
                "user_id": user_id,
                "mileage": request.mileage,
                "condition": request.condition,
                "price": request.price,
                "photo_count": request.photo_count,
                "created_at": now,
                "updated_at": now
            }
        )
        
        db.commit()
        
        # Fetch the created listing to return full details
        listing_result = db.execute(
            text("""
                SELECT id, vehicle_id, lot_id, user_id, mileage, condition, price, photo_count, created_at, updated_at
                FROM listings
                WHERE id = :id
            """),
            {"id": insert_result.lastrowid}
        ).fetchone()
        
        if not listing_result:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve created listing"
            )
        
        # Format timestamps as ISO-8601 strings
        def format_datetime(dt_val):
            if isinstance(dt_val, str):
                # Already a string, ensure it has Z suffix if needed
                if dt_val.endswith("Z"):
                    return dt_val
                # Try to parse and reformat
                try:
                    parsed = datetime.fromisoformat(dt_val.replace("Z", "+00:00"))
                    return parsed.isoformat() + "Z"
                except:
                    return dt_val
            elif isinstance(dt_val, datetime):
                return dt_val.isoformat() + "Z"
            else:
                return str(dt_val)
        
        response = ListingResponse(
            id=listing_result[0],
            vehicle_id=listing_result[1],
            lot_id=listing_result[2],
            user_id=listing_result[3],
            mileage=listing_result[4],
            condition=listing_result[5],
            price=float(listing_result[6]),
            photo_count=listing_result[7],
            created_at=format_datetime(listing_result[8]),
            updated_at=format_datetime(listing_result[9])
        )
        
        return response
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )
    finally:
        db.close()
